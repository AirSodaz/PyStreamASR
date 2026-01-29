from datetime import datetime, timezone
import uuid
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import logging
from sqlalchemy import select, text
from core.config import settings
from models.schemas import Segment

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

    async def get_next_sequence(self) -> int:
        """Atomically increments the sequence counter for this session in Redis.

        Returns:
            int: The new sequence number.
        """
        key = f"asr:sess:{self.session_id}:seq"
        return await redis_client.incr(key)

    async def save_partial(self, text: str, seq: int):
        """Saves the partial draft to Redis.

        Key: asr:sess:{id}:current
        TTL: 300 seconds

        Args:
            text (str): The partial transcription text.
            seq (int): The current sequence number.
        """
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

        # 2. Prepare Segment
        new_segment = Segment(
            id=str(uuid.uuid4()),
            session_id=self.session_id,
            segment_seq=seq,
            content=text,
            created_at=datetime.now(timezone.utc)
        )

        # 3. Insert into MySQL
        async with AsyncSessionLocal() as session:
            async with session.begin():
                session.add(new_segment)
                # Commit is implicit with session.begin() context manager upon exit

        # 4. Delete Draft from Redis
        key = f"asr:sess:{self.session_id}:current"
        await redis_client.delete(key)

        return new_segment
