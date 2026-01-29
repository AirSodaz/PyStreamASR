from datetime import datetime
import uuid
from sqlalchemy import String, Integer, Text, DateTime, Index, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncAttrs


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for SQLAlchemy models using AsyncAttrs."""
    pass


class Session(Base):
    """SQLAlchemy model representing a transcription session.

    Attributes:
        id (str): Unique session identifier.
        user_id (str): ID of the user owning the session.
        created_at (datetime): Timestamp when the session was created.
    """
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Segment(Base):
    """SQLAlchemy model representing a transcribed text segment.

    Attributes:
        id (str): Unique segment identifier (UUID).
        session_id (str): Foreign key to the session.
        segment_seq (int): Sequence number of the segment within the session.
        content (str): The transcribed text content.
        created_at (datetime): Timestamp when the segment was created.
    """
    __tablename__ = "segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id"), nullable=False)
    segment_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Compound index for efficient retrieval of segments by session
    __table_args__ = (
        Index("idx_session_seq", "session_id", "segment_seq"),
    )
