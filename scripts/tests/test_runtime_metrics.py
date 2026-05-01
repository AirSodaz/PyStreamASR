"""Unit tests for runtime metrics aggregation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.metrics import RuntimeMetrics, empty_runtime_metrics_snapshot


class RuntimeMetricsTests(unittest.TestCase):
    """Test process-local runtime metrics counters."""

    def test_empty_snapshot_has_documented_groups(self) -> None:
        """Empty snapshots should expose all runtime metric groups."""
        self.assertEqual(
            empty_runtime_metrics_snapshot(),
            {
                "connections": {
                    "active": 0,
                    "opened": 0,
                    "closed": 0,
                    "disconnected": 0,
                    "errors": 0,
                    "duration_seconds": 0.0,
                },
                "websocket": {
                    "chunks_received": 0,
                    "bytes_received": 0,
                    "receive_errors": 0,
                    "overload_closes": 0,
                },
                "audio": {
                    "processed_chunks": 0,
                    "processing_errors": 0,
                    "processing_seconds": 0.0,
                },
                "transcription": {
                    "partials": 0,
                    "finals": 0,
                    "empty_results": 0,
                    "auto_finalized": 0,
                },
                "storage": {
                    "partial_saves": 0,
                    "final_saves": 0,
                    "save_errors": 0,
                    "partial_seconds": 0.0,
                    "final_seconds": 0.0,
                },
            },
        )

    def test_records_connection_lifecycle_and_runtime_counters(self) -> None:
        """Runtime metrics should aggregate counters without per-session data."""
        metrics = RuntimeMetrics()

        metrics.record_connection_opened()
        metrics.record_websocket_chunk(byte_count=320)
        metrics.record_audio_processed(duration_seconds=0.03)
        metrics.record_partial()
        metrics.record_partial_save(duration_seconds=0.01)
        metrics.record_empty_result()
        metrics.record_final()
        metrics.record_final_save(duration_seconds=0.04)
        metrics.record_auto_finalized()
        metrics.record_overload_close()
        metrics.record_connection_closed(duration_seconds=1.25, disconnected=True)

        snapshot = metrics.snapshot()

        self.assertEqual(snapshot["connections"]["active"], 0)
        self.assertEqual(snapshot["connections"]["opened"], 1)
        self.assertEqual(snapshot["connections"]["closed"], 1)
        self.assertEqual(snapshot["connections"]["disconnected"], 1)
        self.assertEqual(snapshot["connections"]["duration_seconds"], 1.25)
        self.assertEqual(snapshot["websocket"]["chunks_received"], 1)
        self.assertEqual(snapshot["websocket"]["bytes_received"], 320)
        self.assertEqual(snapshot["websocket"]["overload_closes"], 1)
        self.assertEqual(snapshot["audio"]["processed_chunks"], 1)
        self.assertEqual(snapshot["audio"]["processing_seconds"], 0.03)
        self.assertEqual(snapshot["transcription"]["partials"], 1)
        self.assertEqual(snapshot["transcription"]["finals"], 1)
        self.assertEqual(snapshot["transcription"]["empty_results"], 1)
        self.assertEqual(snapshot["transcription"]["auto_finalized"], 1)
        self.assertEqual(snapshot["storage"]["partial_saves"], 1)
        self.assertEqual(snapshot["storage"]["final_saves"], 1)
        self.assertEqual(snapshot["storage"]["partial_seconds"], 0.01)
        self.assertEqual(snapshot["storage"]["final_seconds"], 0.04)
        self.assertNotIn("session", str(snapshot).lower())
        self.assertNotIn("connection_id", str(snapshot).lower())

    def test_records_errors_without_negative_active_connections(self) -> None:
        """Error paths should count failures and keep active connections bounded."""
        metrics = RuntimeMetrics()

        metrics.record_connection_closed(duration_seconds=0.5, error=True)
        metrics.record_receive_error()
        metrics.record_audio_processing_error()
        metrics.record_storage_error()

        snapshot = metrics.snapshot()

        self.assertEqual(snapshot["connections"]["active"], 0)
        self.assertEqual(snapshot["connections"]["closed"], 1)
        self.assertEqual(snapshot["connections"]["errors"], 1)
        self.assertEqual(snapshot["websocket"]["receive_errors"], 1)
        self.assertEqual(snapshot["audio"]["processing_errors"], 1)
        self.assertEqual(snapshot["storage"]["save_errors"], 1)


if __name__ == "__main__":
    unittest.main()
