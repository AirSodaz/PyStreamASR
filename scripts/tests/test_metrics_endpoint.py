"""Unit tests for the process-local metrics endpoint."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import main


class FakeInferenceExecutor:
    """Metrics-capable executor test double."""

    def snapshot(self) -> dict[str, object]:
        """Return a representative inference metrics snapshot."""
        return {
            "max_workers": 2,
            "queue_size": 4,
            "inflight": 1,
            "completed": 3,
            "rejected_overloaded": 1,
            "timed_out": 1,
            "cpu_latency_seconds": 0.25,
            "total_latency_seconds": 0.5,
        }


class FakeRuntimeMetrics:
    """Runtime metrics test double."""

    def snapshot(self) -> dict[str, dict[str, int | float]]:
        """Return representative runtime metrics."""
        return {
            "connections": {
                "active": 1,
                "opened": 2,
                "closed": 1,
                "disconnected": 1,
                "errors": 0,
                "duration_seconds": 3.5,
            },
            "websocket": {
                "chunks_received": 4,
                "bytes_received": 1280,
                "receive_errors": 0,
                "overload_closes": 1,
            },
            "audio": {
                "processed_chunks": 4,
                "processing_errors": 0,
                "processing_seconds": 0.12,
            },
            "transcription": {
                "partials": 3,
                "finals": 1,
                "empty_results": 2,
                "auto_finalized": 1,
            },
            "storage": {
                "partial_saves": 3,
                "final_saves": 1,
                "save_errors": 0,
                "partial_seconds": 0.02,
                "final_seconds": 0.08,
            },
        }


class MetricsEndpointTests(unittest.TestCase):
    """Test simple JSON metrics response shape."""

    def setUp(self) -> None:
        """Preserve process app state before each test."""
        self._original_state = dict(main.app.state._state)

    def tearDown(self) -> None:
        """Restore process app state after each test."""
        main.app.state._state.clear()
        main.app.state._state.update(self._original_state)

    def test_metrics_endpoint_returns_model_status_and_inference_snapshot(self) -> None:
        """Metrics endpoint should return model status and executor counters."""
        main.app.state.model = object()
        main.app.state.inference_executor = FakeInferenceExecutor()
        main.app.state.runtime_metrics = FakeRuntimeMetrics()

        response = main.metrics()

        self.assertEqual(
            response,
            {
                "model_loaded": True,
                "inference": {
                    "max_workers": 2,
                    "queue_size": 4,
                    "inflight": 1,
                    "completed": 3,
                    "rejected_overloaded": 1,
                    "timed_out": 1,
                    "cpu_latency_seconds": 0.25,
                    "total_latency_seconds": 0.5,
                },
                "connections": {
                    "active": 1,
                    "opened": 2,
                    "closed": 1,
                    "disconnected": 1,
                    "errors": 0,
                    "duration_seconds": 3.5,
                },
                "websocket": {
                    "chunks_received": 4,
                    "bytes_received": 1280,
                    "receive_errors": 0,
                    "overload_closes": 1,
                },
                "audio": {
                    "processed_chunks": 4,
                    "processing_errors": 0,
                    "processing_seconds": 0.12,
                },
                "transcription": {
                    "partials": 3,
                    "finals": 1,
                    "empty_results": 2,
                    "auto_finalized": 1,
                },
                "storage": {
                    "partial_saves": 3,
                    "final_saves": 1,
                    "save_errors": 0,
                    "partial_seconds": 0.02,
                    "final_seconds": 0.08,
                },
            },
        )

    def test_metrics_endpoint_falls_back_to_empty_runtime_metrics(self) -> None:
        """Metrics endpoint should keep the runtime shape before lifespan starts."""
        response = main.metrics()

        self.assertFalse(response["model_loaded"])
        self.assertIsNone(response["inference"])
        self.assertEqual(response["connections"]["active"], 0)
        self.assertEqual(response["websocket"]["chunks_received"], 0)
        self.assertEqual(response["audio"]["processing_errors"], 0)
        self.assertEqual(response["transcription"]["finals"], 0)
        self.assertEqual(response["storage"]["save_errors"], 0)


if __name__ == "__main__":
    unittest.main()
