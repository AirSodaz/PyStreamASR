"""Unit tests for logging correlation context."""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.context import connection_id_ctx, session_id_ctx
from core.logging import CorrelationIdFilter


class LoggingContextTests(unittest.TestCase):
    """Test log-record correlation fields."""

    def make_record(self) -> logging.LogRecord:
        """Create a plain log record for filter tests."""
        return logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )

    def test_correlation_filter_uses_default_placeholders(self) -> None:
        """Log records outside a request should use placeholder IDs."""
        record = self.make_record()

        self.assertTrue(CorrelationIdFilter().filter(record))

        self.assertEqual(record.session_id, "-")
        self.assertEqual(record.connection_id, "-")

    def test_correlation_filter_injects_session_and_connection_ids(self) -> None:
        """Log records should include both session and connection context."""
        session_token = session_id_ctx.set("session-1")
        connection_token = connection_id_ctx.set("conn-abc123")
        try:
            record = self.make_record()

            self.assertTrue(CorrelationIdFilter().filter(record))

            self.assertEqual(record.session_id, "session-1")
            self.assertEqual(record.connection_id, "conn-abc123")
        finally:
            connection_id_ctx.reset(connection_token)
            session_id_ctx.reset(session_token)


if __name__ == "__main__":
    unittest.main()
