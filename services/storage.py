from dataclasses import dataclass
from datetime import datetime, timezone
import uuid
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import logging
import time
from typing import Dict
from sqlalchemy import func, select, text
from core.config import settings
from services.schemas import Segment, Session

# Database Setup
engine = create_async_engine(settings.MYSQL_DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

@dataclass
class PartialEntry:
    """In-memory partial transcription entry."""
    content: str
    seq: int
    ts_iso: str
    expires_at: float


_PARTIAL_TTL_SECONDS = 300.0
_SEQ_BY_SESSION: Dict[str, int] = {}
_PARTIAL_BY_SESSION: Dict[str, PartialEntry] = {}
_CACHE_LOCK: asyncio.Lock | None = None


def _get_cache_lock() -> asyncio.Lock:
    global _CACHE_LOCK
    if _CACHE_LOCK is None:
        _CACHE_LOCK = asyncio.Lock()
    return _CACHE_LOCK


def _cleanup_expired_partials(now: float) -> None:
    expired_sessions = [
        session_id
        for session_id, entry in _PARTIAL_BY_SESSION.items()
        if entry.expires_at <= now
    ]
    for session_id in expired_sessions:
        _PARTIAL_BY_SESSION.pop(session_id, None)


async def check_database_connections():
    """Checks connection to MySQL and validates in-memory cache state."""
    try:
        logging.info("Checking database connections...")
        # Check MySQL
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        logging.info("MySQL connection successful.")
        logging.info("In-memory cache available.")

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
        """Atomically increments the sequence counter for this session in memory.

        Returns:
            int: The new sequence number.
        """
        start_time = time.perf_counter()
        async with _get_cache_lock():
            current = _SEQ_BY_SESSION.get(self.session_id, 0) + 1
            _SEQ_BY_SESSION[self.session_id] = current
            res = current
        duration = time.perf_counter() - start_time
        logging.debug(f"[Storage] Memory INCR took {duration:.6f}s. New Seq: {res}")
        return res

    async def get_current_sequence(self) -> int:
        """Gets the current sequence counter for this session.

        In-memory state is used as the hot path. When the process has restarted
        or the session is otherwise missing from memory, the counter is restored
        from the current database maximum so partial sequence numbers continue
        after persisted final segments.

        Returns:
            int: The current sequence number.
        """
        start_time = time.perf_counter()
        async with _get_cache_lock():
            cached = _SEQ_BY_SESSION.get(self.session_id)
            if cached is not None:
                duration = time.perf_counter() - start_time
                logging.debug(
                    f"[Storage] Memory GET took {duration:.6f}s. Current Seq: {cached}"
                )
                return cached

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(func.max(Segment.segment_seq))
                .where(Segment.session_id == self.session_id)
            )
            max_seq = result.scalar_one()

        restored = max_seq or 0
        async with _get_cache_lock():
            current = max(_SEQ_BY_SESSION.get(self.session_id, 0), restored)
            _SEQ_BY_SESSION[self.session_id] = current

        duration = time.perf_counter() - start_time
        logging.debug(
            f"[Storage] Sequence restore took {duration:.6f}s. Current Seq: {current}"
        )
        return current

    async def save_partial(self, text: str, seq: int):
        """Saves the partial draft to in-memory cache.

        Key: asr:sess:{id}:current
        TTL: 300 seconds

        Args:
            text (str): The partial transcription text.
            seq (int): The current sequence number.
        """
        start_time = time.perf_counter()
        now = time.monotonic()
        entry = PartialEntry(
            content=text,
            seq=seq,
            ts_iso=datetime.now(timezone.utc).isoformat(),
            expires_at=now + _PARTIAL_TTL_SECONDS
        )
        async with _get_cache_lock():
            _cleanup_expired_partials(now)
            _PARTIAL_BY_SESSION[self.session_id] = entry

        duration = time.perf_counter() - start_time
        logging.debug(f"[Storage] save_partial (Memory) took {duration:.6f}s")

    async def save_final(self, text: str) -> Segment:
        """Persists the final segment to MySQL and clears the cached draft.

        The segment is inserted inside a database transaction before this
        method returns. Sequence allocation is based on the current database
        maximum while holding a row lock on the parent session.

        Args:
            text (str): The final transcription text.

        Returns:
            Segment: The persisted segment object.
        """
        await self.ensure_session_exists()

        start_time = time.perf_counter()
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(Session)
                    .where(Session.id == self.session_id)
                    .with_for_update()
                )
                existing_session = result.scalar_one_or_none()
                if existing_session is None:
                    raise RuntimeError(
                        f"Session {self.session_id} could not be locked for final save"
                    )

                max_result = await session.execute(
                    select(func.max(Segment.segment_seq))
                    .where(Segment.session_id == self.session_id)
                )
                max_seq = max_result.scalar_one()
                seq = (max_seq or 0) + 1

                new_segment = Segment(
                    id=str(uuid.uuid4()),
                    session_id=self.session_id,
                    segment_seq=seq,
                    content=text,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(new_segment)

        db_duration = time.perf_counter() - start_time
        logging.debug(
            f"[Storage] Final MySQL insert took {db_duration:.6f}s. "
            f"Session: {self.session_id}"
        )

        cache_start = time.perf_counter()
        async with _get_cache_lock():
            _PARTIAL_BY_SESSION.pop(self.session_id, None)
            _SEQ_BY_SESSION[self.session_id] = new_segment.segment_seq
        cache_duration = time.perf_counter() - cache_start
        logging.debug(f"[Storage] Final cache update took {cache_duration:.6f}s")

        return new_segment
