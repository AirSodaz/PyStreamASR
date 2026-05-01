"""Unit tests for ASR inference concurrency and backpressure."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np
from pydantic import ValidationError

ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from api import endpoints
from core import config
from services import inference


class FakeStream:
    """Sherpa stream test double."""

    def __init__(self) -> None:
        """Initialize captured waveform state."""
        self.accepted_sample_rate: int | None = None
        self.accepted_samples: np.ndarray | None = None

    def accept_waveform(self, sample_rate: int, samples: np.ndarray) -> None:
        """Capture waveform input."""
        self.accepted_sample_rate = sample_rate
        self.accepted_samples = samples


class FakeRecognizer:
    """Sherpa recognizer test double."""

    def __init__(self) -> None:
        """Initialize recognizer state."""
        self.stream = FakeStream()
        self.ready_calls = 0
        self.decode_calls = 0
        self.reset_calls = 0

    def create_stream(self) -> FakeStream:
        """Return the fake stream."""
        return self.stream

    def is_ready(self, stream: FakeStream) -> bool:
        """Return ready once so infer exercises decode_stream."""
        self.ready_calls += 1
        return self.ready_calls == 1

    def decode_stream(self, stream: FakeStream) -> None:
        """Track decode calls."""
        self.decode_calls += 1

    def get_result(self, stream: FakeStream) -> str:
        """Return a padded transcript to verify stripping."""
        return " hello "

    def is_endpoint(self, stream: FakeStream) -> bool:
        """Mark the result as final."""
        return True

    def reset(self, stream: FakeStream) -> None:
        """Track stream resets."""
        self.reset_calls += 1


class FakeWebSocket:
    """Minimal WebSocket test double for overload responses."""

    def __init__(self) -> None:
        """Initialize captured messages."""
        self.messages: list[dict[str, object]] = []
        self.close_code: int | None = None
        self.close_reason: str = ""

    async def send_json(self, payload: dict[str, object]) -> None:
        """Capture sent JSON."""
        self.messages.append(payload)

    async def close(self, code: int, reason: str) -> None:
        """Capture close metadata."""
        self.close_code = code
        self.close_reason = reason


class InferenceBackpressureTests(unittest.TestCase):
    """Test ASR inference backpressure behavior."""

    def override_attr(self, obj: object, name: str, value: object) -> None:
        """Temporarily replace an attribute."""
        original_value = getattr(obj, name)
        self.addCleanup(setattr, obj, name, original_value)
        setattr(obj, name, value)

    def write_env(self, *extra_lines: str) -> Path:
        """Create a minimal env file for settings tests."""
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        env_file = Path(temp_dir.name) / ".env"
        lines = [
            "PROJECT_NAME=PyStreamASR",
            "MYSQL_DATABASE_URL=mysql+aiomysql://root:password@localhost/pystreamasr",
            "MODEL_PATH=models/test-model",
            *extra_lines,
        ]
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return env_file

    def test_asr_inference_defaults_are_throughput_oriented(self) -> None:
        """ASR inference defaults should derive queue size from worker count."""
        self.override_attr(config.os, "cpu_count", lambda: 8)

        settings = config.get_settings(self.write_env())

        self.assertEqual(settings.ASR_INFERENCE_WORKERS, 4)
        self.assertEqual(settings.ASR_INFERENCE_QUEUE_SIZE, 16)
        self.assertEqual(settings.ASR_INFERENCE_QUEUE_TIMEOUT_SECONDS, 20.0)

    def test_asr_inference_settings_validate_bounds(self) -> None:
        """Invalid ASR inference settings should fail validation."""
        invalid_lines = [
            "ASR_INFERENCE_WORKERS=0",
            "ASR_INFERENCE_QUEUE_SIZE=-1",
            "ASR_INFERENCE_QUEUE_TIMEOUT_SECONDS=0",
        ]

        for line in invalid_lines:
            with self.subTest(line=line):
                with self.assertRaises(ValidationError):
                    config.get_settings(self.write_env(line))

    def test_bounded_executor_limits_concurrent_inference(self) -> None:
        """Executor should not run more blocking calls than max_workers."""
        async def scenario() -> None:
            executor = inference.BoundedInferenceExecutor(
                max_workers=2,
                queue_size=2,
                queue_timeout_seconds=1.0,
            )
            active = 0
            max_active = 0
            lock = threading.Lock()

            def blocking_call() -> str:
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return "ok"

            try:
                results = await asyncio.gather(
                    *(executor.run(blocking_call) for _ in range(4))
                )
            finally:
                executor.shutdown()

            self.assertEqual(results, ["ok", "ok", "ok", "ok"])
            self.assertLessEqual(max_active, 2)

        asyncio.run(scenario())

    def test_bounded_executor_rejects_when_capacity_is_full(self) -> None:
        """Executor should reject immediately when running plus queued is full."""
        async def wait_for_inflight(
            executor: inference.BoundedInferenceExecutor,
            expected: int,
        ) -> None:
            for _ in range(100):
                async with executor._capacity_lock:
                    if executor._inflight == expected:
                        return
                await asyncio.sleep(0.01)
            self.fail(f"Timed out waiting for inflight={expected}")

        async def scenario() -> None:
            executor = inference.BoundedInferenceExecutor(
                max_workers=1,
                queue_size=1,
                queue_timeout_seconds=1.0,
            )
            started = threading.Event()
            release = threading.Event()

            def blocking_call() -> str:
                started.set()
                release.wait(timeout=2.0)
                return "released"

            try:
                first = asyncio.create_task(executor.run(blocking_call))
                await asyncio.to_thread(started.wait, 1.0)
                second = asyncio.create_task(executor.run(lambda: "queued"))
                await wait_for_inflight(executor, 2)

                with self.assertRaises(inference.InferenceOverloadedError):
                    await executor.run(lambda: "rejected")

                release.set()
                self.assertEqual(await first, "released")
                self.assertEqual(await second, "queued")
            finally:
                release.set()
                executor.shutdown()

        asyncio.run(scenario())

    def test_bounded_executor_times_out_waiting_for_worker(self) -> None:
        """Queued inference should fail when it waits longer than configured."""
        async def scenario() -> None:
            executor = inference.BoundedInferenceExecutor(
                max_workers=1,
                queue_size=1,
                queue_timeout_seconds=0.05,
            )
            started = threading.Event()
            release = threading.Event()

            def blocking_call() -> str:
                started.set()
                release.wait(timeout=2.0)
                return "released"

            try:
                first = asyncio.create_task(executor.run(blocking_call))
                await asyncio.to_thread(started.wait, 1.0)

                with self.assertRaises(inference.InferenceQueueTimeoutError):
                    await executor.run(lambda: "too-late")

                release.set()
                self.assertEqual(await first, "released")
            finally:
                release.set()
                executor.shutdown()

        asyncio.run(scenario())

    def test_inference_service_uses_bounded_executor(self) -> None:
        """ASRInferenceService should still return text and final state."""
        async def scenario() -> None:
            executor = inference.BoundedInferenceExecutor(
                max_workers=1,
                queue_size=1,
                queue_timeout_seconds=1.0,
            )
            recognizer = FakeRecognizer()
            service = inference.ASRInferenceService(recognizer, executor)

            try:
                text, is_final = await service.infer(
                    np.array([0.1, 0.2], dtype=np.float32)
                )
            finally:
                executor.shutdown()

            self.assertEqual(text, "hello")
            self.assertTrue(is_final)
            self.assertEqual(recognizer.stream.accepted_sample_rate, 16000)
            self.assertEqual(recognizer.decode_calls, 1)
            self.assertEqual(recognizer.reset_calls, 1)

        asyncio.run(scenario())

    def test_websocket_overload_helper_sends_error_and_1013_close(self) -> None:
        """Overload helper should send the documented error event and close code."""
        async def scenario() -> FakeWebSocket:
            websocket = FakeWebSocket()
            await endpoints._send_inference_overload_error(websocket)
            return websocket

        websocket = asyncio.run(scenario())

        self.assertEqual(
            websocket.messages,
            [
                {
                    "type": "error",
                    "code": inference.INFERENCE_OVERLOAD_ERROR_CODE,
                    "message": inference.INFERENCE_OVERLOAD_MESSAGE,
                    "retryable": True,
                }
            ],
        )
        self.assertEqual(websocket.close_code, inference.INFERENCE_OVERLOAD_CLOSE_CODE)
        self.assertEqual(websocket.close_reason, inference.INFERENCE_OVERLOAD_CLOSE_REASON)


if __name__ == "__main__":
    unittest.main()
