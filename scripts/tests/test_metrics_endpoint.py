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
            },
        )


if __name__ == "__main__":
    unittest.main()
