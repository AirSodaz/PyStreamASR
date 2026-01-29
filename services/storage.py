from datetime import datetime, timezone
import uuid
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select
from core.config import settings
from models.schemas import Segment

# Database Setup
engine = create_async_engine(settings.MYSQL_DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Redis Setup
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

class StorageManager:
    def __init__(self, session_id: str):
        self.session_id = session_id

    async def get_next_sequence(self) -> int:
        """
        Atomically increments the sequence counter for this session in Redis.
        Returns the new sequence number.
        """
        key = f"asr:sess:{self.session_id}:seq"
        return await redis_client.incr(key)

    async def save_partial(self, text: str, seq: int):
        """
        Saves the partial draft to Redis.
        Key: asr:sess:{id}:current
        TTL: 300 seconds
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
        """
        1. Generates UUID.
        2. Gets next sequence number.
        3. Persists to MySQL `Segment` table.
        4. Deletes the 'current' draft from Redis.
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
