"""Unit tests for concurrent stream report helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"

for import_path in (ROOT_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from scripts.simulate_concurrent_streams import (
    StreamStats,
    build_report,
    classify_stream,
)


class ConcurrentStreamReportTests(unittest.TestCase):
    """Test JSON-friendly load-test report generation."""

    def test_classifies_successful_stream_without_errors_or_overload(self) -> None:
        """A clean stream is classified as successful."""
        stats = StreamStats(
            stream_id="stream-ok",
            chunks_sent=3,
            messages_received=2,
            finals_received=1,
            start_time=10.0,
            end_time=12.5,
        )

        self.assertEqual(classify_stream(stats), "successful")

    def test_classifies_overload_from_event_or_close_code(self) -> None:
        """Overload signals take priority over generic failure state."""
        overloaded_event = StreamStats(
            stream_id="stream-overload-event",
            overloads_received=1,
            errors_received=1,
            error="Server error: inference_overloaded",
        )
        overloaded_close = StreamStats(
            stream_id="stream-overload-close",
            close_code=1013,
            close_reason="try again later",
        )

        self.assertEqual(classify_stream(overloaded_event), "overloaded")
        self.assertEqual(classify_stream(overloaded_close), "overloaded")

    def test_classifies_failed_stream_for_non_overload_errors(self) -> None:
        """Non-overload connection and server errors are failures."""
        stats = StreamStats(
            stream_id="stream-failed",
            errors_received=1,
            error="Server error: internal_error",
        )

        self.assertEqual(classify_stream(stats), "failed")

    def test_build_report_counts_and_includes_every_stream(self) -> None:
        """The structured report contains aggregate counts and stream details."""
        stats_list = [
            StreamStats(
                stream_id="stream-ok",
                chunks_sent=5,
                messages_received=4,
                partials_received=3,
                finals_received=1,
                start_time=1.0,
                end_time=3.25,
            ),
            StreamStats(
                stream_id="stream-overloaded",
                overloads_received=1,
                errors_received=1,
                close_code=1013,
                close_reason="busy",
                error="Server error: inference_overloaded",
                start_time=2.0,
                end_time=4.0,
            ),
            StreamStats(
                stream_id="stream-failed",
                errors_received=1,
                error="Connection error: refused",
            ),
        ]

        report = build_report(stats_list, total_time=6.5)

        self.assertEqual(report["total_streams"], 3)
        self.assertEqual(report["successful"], 1)
        self.assertEqual(report["overloaded"], 1)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["total_time_seconds"], 6.5)
        self.assertEqual([stream["stream_id"] for stream in report["streams"]], [
            "stream-ok",
            "stream-overloaded",
            "stream-failed",
        ])
        self.assertEqual([stream["status"] for stream in report["streams"]], [
            "successful",
            "overloaded",
            "failed",
        ])
        self.assertEqual(report["streams"][0]["duration_seconds"], 2.25)
        self.assertEqual(report["streams"][1]["close_reason"], "busy")
        self.assertEqual(report["streams"][2]["error"], "Connection error: refused")


if __name__ == "__main__":
    unittest.main()
