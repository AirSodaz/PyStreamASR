from datetime import datetime, timezone
import uuid
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import logging
import time
from sqlalchemy import select, text
from core.config import settings
from services.schemas import Segment, Session

# Database Setup
engine = create_async_engine(settings.MYSQL_DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Redis Setup
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


async def check_database_connections():
    """Checks connections to MySQL and Redis."""
    try:
        logging.info("Checking database connections...")
        # Check MySQL
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        logging.info("MySQL connection successful.")

        # Check Redis
        await redis_client.ping()
        logging.info("Redis connection successful.")

    except Exception as e:
        logging.error(f"Database connection failed: {e}")
        raise e


class StorageManager:
    def __init__(self, session_id: str):
        self.session_id = session_id

    async def ensure_session_exists(self, user_id: str = "anonymous"):
        """Ensures the session exists in the database. If not, creates it.

        Args:
            user_id (str): The user ID associated with the session. Defaults to "anonymous".
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                start_time = time.perf_counter()
                result = await session.execute(select(Session).where(Session.id == self.session_id))
                existing_session = result.scalar_one_or_none()

                if not existing_session:
                    new_session = Session(
                        id=self.session_id,
                        user_id=user_id,
                        created_at=datetime.now(timezone.utc)
                    )
                    session.add(new_session)
                    logging.info(f"Created new session: {self.session_id}")
                else:
                    logging.debug(f"Found existing session: {self.session_id}")

                duration = time.perf_counter() - start_time
                logging.debug(f"[Storage] ensure_session_exists took {duration:.6f}s")


    async def get_next_sequence(self) -> int:
        """Atomically increments the sequence counter for this session in Redis.

        Returns:
            int: The new sequence number.
        """
        start_time = time.perf_counter()
        key = f"asr:sess:{self.session_id}:seq"
        res = await redis_client.incr(key)
        duration = time.perf_counter() - start_time
        logging.debug(f"[Storage] Redis INCR took {duration:.6f}s. New Seq: {res}")
        return res

    async def save_partial(self, text: str, seq: int):
        """Saves the partial draft to Redis.

        Key: asr:sess:{id}:current
        TTL: 300 seconds

        Args:
            text (str): The partial transcription text.
            seq (int): The current sequence number.
        """
        start_time = time.perf_counter()
        key = f"asr:sess:{self.session_id}:current"
        mapping = {
            "content": text,
            "seq": seq,
            "ts": datetime.now(timezone.utc).isoformat()
        }
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, 300)
            await pipe.execute()

        duration = time.perf_counter() - start_time
        logging.debug(f"[Storage] save_partial (Redis) took {duration:.6f}s")

    async def save_final(self, text: str):
        """Persists the final segment to MySQL and clears the Redis draft.

        1. Generates UUID.
        2. Gets next sequence number.
        3. Persists to MySQL `Segment` table.
        4. Deletes the 'current' draft from Redis.

        Args:
            text (str): The final transcription text.

        Returns:
            Segment: The saved segment object.
        """
        # 1. Get Sequence
        seq = await self.get_next_sequence()

        start_time = time.perf_counter()

        # 2. Prepare Segment
        new_segment = Segment(
            id=str(uuid.uuid4()),
            session_id=self.session_id,
            segment_seq=seq,
            content=text,
            created_at=datetime.now(timezone.utc)
        )

        # Log params at DEBUG
        params = {
            "id": new_segment.id,
            "session_id": new_segment.session_id,
            "segment_seq": new_segment.segment_seq,
            "content": new_segment.content,
            "created_at": str(new_segment.created_at)
        }
        logging.debug(f"[Storage] Inserting Segment params: {params}")

        # 3. Insert into MySQL
        async with AsyncSessionLocal() as session:
            async with session.begin():
                session.add(new_segment)
                # Commit is implicit with session.begin() context manager upon exit

        db_duration = time.perf_counter() - start_time
        logging.debug(f"[Storage] save_final (MySQL) took {db_duration:.6f}s")

        # 4. Delete Draft from Redis
        redis_start = time.perf_counter()
        key = f"asr:sess:{self.session_id}:current"
        await redis_client.delete(key)
        redis_duration = time.perf_counter() - redis_start
        logging.debug(f"[Storage] Redis DELETE took {redis_duration:.6f}s")

        return new_segment
