"""Process-local runtime metrics for the ASR service."""

from __future__ import annotations

import threading
from copy import deepcopy

RuntimeMetricsSnapshot = dict[str, dict[str, int | float]]


def empty_runtime_metrics_snapshot() -> RuntimeMetricsSnapshot:
    """Return the documented empty runtime metrics shape."""
    return {
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
    }


class RuntimeMetrics:
    """Aggregate process-local runtime counters.

    The snapshot intentionally avoids per-session and per-connection keys so
    the metrics endpoint stays low-cardinality and safe to expose operationally.
    """

    def __init__(self) -> None:
        """Initialize empty counters."""
        self._lock = threading.Lock()
        self._snapshot = empty_runtime_metrics_snapshot()

    def snapshot(self) -> RuntimeMetricsSnapshot:
        """Return a JSON-friendly metrics snapshot."""
        with self._lock:
            return deepcopy(self._snapshot)

    def record_connection_opened(self) -> None:
        """Record an accepted WebSocket connection."""
        with self._lock:
            self._snapshot["connections"]["active"] += 1
            self._snapshot["connections"]["opened"] += 1

    def record_connection_closed(
        self,
        duration_seconds: float,
        *,
        disconnected: bool = False,
        error: bool = False,
    ) -> None:
        """Record a closed WebSocket connection."""
        with self._lock:
            connections = self._snapshot["connections"]
            connections["active"] = max(0, int(connections["active"]) - 1)
            connections["closed"] += 1
            connections["duration_seconds"] += duration_seconds
            if disconnected:
                connections["disconnected"] += 1
            if error:
                connections["errors"] += 1

    def record_websocket_chunk(self, byte_count: int) -> None:
        """Record a received WebSocket audio chunk."""
        with self._lock:
            self._snapshot["websocket"]["chunks_received"] += 1
            self._snapshot["websocket"]["bytes_received"] += byte_count

    def record_receive_error(self) -> None:
        """Record a WebSocket receive error."""
        with self._lock:
            self._snapshot["websocket"]["receive_errors"] += 1

    def record_overload_close(self) -> None:
        """Record a connection closed because inference capacity was exhausted."""
        with self._lock:
            self._snapshot["websocket"]["overload_closes"] += 1

    def record_audio_processed(self, duration_seconds: float) -> None:
        """Record a successfully processed audio chunk."""
        with self._lock:
            self._snapshot["audio"]["processed_chunks"] += 1
            self._snapshot["audio"]["processing_seconds"] += duration_seconds

    def record_audio_processing_error(self) -> None:
        """Record an audio processing failure."""
        with self._lock:
            self._snapshot["audio"]["processing_errors"] += 1

    def record_partial(self) -> None:
        """Record an emitted or tracked partial transcription."""
        with self._lock:
            self._snapshot["transcription"]["partials"] += 1

    def record_final(self) -> None:
        """Record an emitted or tracked final transcription."""
        with self._lock:
            self._snapshot["transcription"]["finals"] += 1

    def record_empty_result(self) -> None:
        """Record an inference call that returned no text."""
        with self._lock:
            self._snapshot["transcription"]["empty_results"] += 1

    def record_auto_finalized(self) -> None:
        """Record a pending partial finalized during connection cleanup."""
        with self._lock:
            self._snapshot["transcription"]["auto_finalized"] += 1

    def record_partial_save(self, duration_seconds: float) -> None:
        """Record a successful partial save."""
        with self._lock:
            self._snapshot["storage"]["partial_saves"] += 1
            self._snapshot["storage"]["partial_seconds"] += duration_seconds

    def record_final_save(self, duration_seconds: float) -> None:
        """Record a successful final save."""
        with self._lock:
            self._snapshot["storage"]["final_saves"] += 1
            self._snapshot["storage"]["final_seconds"] += duration_seconds

    def record_storage_error(self) -> None:
        """Record a storage failure."""
        with self._lock:
            self._snapshot["storage"]["save_errors"] += 1
