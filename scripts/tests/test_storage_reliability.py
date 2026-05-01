"""Unit tests for final-segment storage reliability."""

from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import UniqueConstraint

ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services import storage
from services.schemas import Segment, Session


class FakeScalarResult:
    """SQLAlchemy scalar-result test double."""

    def __init__(self, value: Any) -> None:
        """Store the scalar value returned by the fake query."""
        self.value = value

    def scalar_one_or_none(self) -> Any:
        """Return the configured scalar-or-none value."""
        return self.value

    def scalar_one(self) -> Any:
        """Return the configured scalar value."""
        return self.value


@dataclass
class FakeDatabase:
    """In-memory database state for storage tests."""

    sessions: dict[str, Session] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    fail_segment_add: bool = False

    def max_seq(self, session_id: str) -> int | None:
        """Return the maximum stored sequence for a session."""
        values = [
            segment.segment_seq
            for segment in self.segments
            if segment.session_id == session_id
        ]
        return max(values) if values else None


class FakeAsyncSession:
    """Minimal async SQLAlchemy session test double."""

    def __init__(self, database: FakeDatabase) -> None:
        """Bind the session to shared fake database state."""
        self.database = database

    async def __aenter__(self) -> FakeAsyncSession:
        """Enter the async session context."""
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Exit the async session context."""

    def begin(self) -> FakeAsyncSession:
        """Return a transaction context manager."""
        return self

    async def execute(self, statement: Any) -> FakeScalarResult:
        """Return fake results for session lookup and max-sequence queries."""
        statement_text = str(statement)
        if "max(" in statement_text:
            session_id = self._extract_bound_session_id(statement)
            return FakeScalarResult(self.database.max_seq(session_id))

        session_id = self._extract_bound_session_id(statement)
        return FakeScalarResult(self.database.sessions.get(session_id))

    def add(self, instance: object) -> None:
        """Insert fake ORM instances into the in-memory database."""
        if isinstance(instance, Session):
            self.database.sessions.setdefault(instance.id, instance)
            return

        if isinstance(instance, Segment):
            if self.database.fail_segment_add:
                raise RuntimeError("simulated insert failure")
            for existing in self.database.segments:
                if (
                    existing.session_id == instance.session_id
                    and existing.segment_seq == instance.segment_seq
                ):
                    raise RuntimeError("duplicate segment sequence")
            self.database.segments.append(instance)
            return

        raise TypeError(f"Unexpected instance type: {type(instance)!r}")

    def _extract_bound_session_id(self, statement: Any) -> str:
        """Read the first session-id bind parameter from a SQLAlchemy statement."""
        compiled = statement.compile()
        for value in compiled.params.values():
            if isinstance(value, str):
                return value
        raise AssertionError(f"Could not find session id in statement: {statement}")


class StorageReliabilityTests(unittest.TestCase):
    """Test final persistence and sequence allocation behavior."""

    def setUp(self) -> None:
        """Reset storage globals and patch the DB session factory."""
        storage._SEQ_BY_SESSION.clear()
        storage._PARTIAL_BY_SESSION.clear()
        self.database = FakeDatabase()
        self.patch_attr(
            storage,
            "AsyncSessionLocal",
            lambda: FakeAsyncSession(self.database),
        )

    def patch_attr(self, obj: object, name: str, value: object) -> None:
        """Temporarily replace an attribute."""
        original_value = getattr(obj, name)
        self.addCleanup(setattr, obj, name, original_value)
        setattr(obj, name, value)

    def test_final_save_failure_does_not_return_fake_success(self) -> None:
        """A failed final insert should raise instead of returning an unsaved Segment."""
        async def scenario() -> None:
            manager = storage.StorageManager("failed-session")
            await manager.save_partial("draft", 1)
            self.database.fail_segment_add = True

            with self.assertRaises(RuntimeError):
                await manager.save_final("final text")

            self.assertEqual(self.database.segments, [])
            self.assertIn("failed-session", storage._PARTIAL_BY_SESSION)

        asyncio.run(scenario())

    def test_sequence_recovers_from_db_max_after_memory_restart(self) -> None:
        """Final save should allocate the next sequence from the database max."""
        async def scenario() -> None:
            self.database.sessions["recover-session"] = Session(
                id="recover-session",
                user_id="anonymous",
            )
            self.database.segments.append(
                Segment(
                    id="existing-segment",
                    session_id="recover-session",
                    segment_seq=7,
                    content="already stored",
                )
            )
            storage._SEQ_BY_SESSION.clear()

            segment = await storage.StorageManager("recover-session").save_final("new")

            self.assertEqual(segment.segment_seq, 8)
            self.assertEqual(storage._SEQ_BY_SESSION["recover-session"], 8)

        asyncio.run(scenario())

    def test_current_sequence_recovers_from_db_max_after_memory_restart(self) -> None:
        """Current sequence lookup should recover from DB state for partials."""
        async def scenario() -> None:
            self.database.sessions["partial-recover-session"] = Session(
                id="partial-recover-session",
                user_id="anonymous",
            )
            self.database.segments.append(
                Segment(
                    id="existing-partial-segment",
                    session_id="partial-recover-session",
                    segment_seq=4,
                    content="already stored",
                )
            )
            storage._SEQ_BY_SESSION.clear()

            current = await storage.StorageManager(
                "partial-recover-session"
            ).get_current_sequence()

            self.assertEqual(current, 4)
            self.assertEqual(storage._SEQ_BY_SESSION["partial-recover-session"], 4)

        asyncio.run(scenario())

    def test_same_session_sequential_saves_do_not_duplicate_sequence(self) -> None:
        """Sequential final saves for one session should persist distinct seq values."""
        async def scenario() -> None:
            manager = storage.StorageManager("same-session")

            first = await manager.save_final("first")
            second = await manager.save_final("second")

            self.assertEqual([first.segment_seq, second.segment_seq], [1, 2])
            self.assertEqual(
                [segment.segment_seq for segment in self.database.segments],
                [1, 2],
            )

        asyncio.run(scenario())

    def test_segments_has_unique_session_sequence_constraint(self) -> None:
        """Segments should reject duplicate sequence numbers per session at DB level."""
        constraints = [
            item
            for item in Segment.__table_args__
            if isinstance(item, UniqueConstraint)
        ]

        self.assertTrue(
            any(
                constraint.name == "uq_segments_session_seq"
                and {column.name for column in constraint.columns}
                == {"session_id", "segment_seq"}
                for constraint in constraints
            )
        )


if __name__ == "__main__":
    unittest.main()
