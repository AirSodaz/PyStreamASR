import asyncio
import sherpa_onnx
import numpy as np
import logging
import time
import contextvars
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Tuple, Any, TypeVar

from core.config import PROJECT_ROOT, Settings, settings

MODEL_REQUIRED_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt")
INFERENCE_OVERLOAD_ERROR_CODE = "inference_overloaded"
INFERENCE_OVERLOAD_MESSAGE = "ASR inference is overloaded; retry later."
INFERENCE_OVERLOAD_CLOSE_CODE = 1013
INFERENCE_OVERLOAD_CLOSE_REASON = "inference overloaded"
T = TypeVar("T")


class InferenceBackpressureError(RuntimeError):
    """Base error for ASR inference backpressure failures."""


class InferenceOverloadedError(InferenceBackpressureError):
    """Raised when the inference executor has no remaining queue capacity."""


class InferenceQueueTimeoutError(InferenceBackpressureError):
    """Raised when queued inference waits longer than the configured timeout."""


class BoundedInferenceExecutor:
    """Run CPU-bound ASR inference through a bounded thread pool."""

    def __init__(
        self,
        max_workers: int,
        queue_size: int,
        queue_timeout_seconds: float,
    ) -> None:
        """Initialize the bounded executor.

        Args:
            max_workers: Maximum number of concurrently running inference calls.
            queue_size: Number of additional inference calls allowed to wait.
            queue_timeout_seconds: Maximum time a queued call may wait for a worker.
        """
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if queue_size < 0:
            raise ValueError("queue_size must be greater than or equal to 0")
        if queue_timeout_seconds <= 0:
            raise ValueError("queue_timeout_seconds must be greater than 0")

        self.max_workers = max_workers
        self.queue_size = queue_size
        self.queue_timeout_seconds = queue_timeout_seconds
        self._capacity = max_workers + queue_size
        self._inflight = 0
        self._capacity_lock = asyncio.Lock()
        self._worker_slots = asyncio.Semaphore(max_workers)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="asr-inference",
        )
        self._closed = False

    async def run(self, func: Callable[[], T]) -> T:
        """Run a blocking inference function with bounded queueing.

        Args:
            func: Blocking function to execute in the inference thread pool.

        Returns:
            The function result.

        Raises:
            InferenceOverloadedError: If running plus queued work is at capacity.
            InferenceQueueTimeoutError: If queued work waits too long.
        """
        await self._reserve_capacity()
        worker_acquired = False
        try:
            try:
                await asyncio.wait_for(
                    self._worker_slots.acquire(),
                    timeout=self.queue_timeout_seconds,
                )
                worker_acquired = True
            except asyncio.TimeoutError as exc:
                raise InferenceQueueTimeoutError(
                    "Timed out waiting for an ASR inference worker."
                ) from exc

            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(self._executor, func)
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                try:
                    await future
                except Exception:
                    pass
                raise
        finally:
            if worker_acquired:
                self._worker_slots.release()
            await self._release_capacity()

    async def _reserve_capacity(self) -> None:
        """Reserve one running-or-queued inference slot."""
        async with self._capacity_lock:
            if self._closed:
                raise InferenceOverloadedError("ASR inference executor is shut down.")
            if self._inflight >= self._capacity:
                raise InferenceOverloadedError(
                    "ASR inference worker and queue capacity is exhausted."
                )
            self._inflight += 1

    async def _release_capacity(self) -> None:
        """Release one running-or-queued inference slot."""
        async with self._capacity_lock:
            if self._inflight > 0:
                self._inflight -= 1

    def shutdown(self) -> None:
        """Stop accepting new inference work and shut down the thread pool."""
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


def create_inference_executor(runtime_settings: Settings = settings) -> BoundedInferenceExecutor:
    """Create the shared ASR inference executor from settings."""
    queue_size = runtime_settings.ASR_INFERENCE_QUEUE_SIZE
    if queue_size is None:
        queue_size = runtime_settings.ASR_INFERENCE_WORKERS * 4

    executor = BoundedInferenceExecutor(
        max_workers=runtime_settings.ASR_INFERENCE_WORKERS,
        queue_size=queue_size,
        queue_timeout_seconds=runtime_settings.ASR_INFERENCE_QUEUE_TIMEOUT_SECONDS,
    )
    logging.info(
        "ASR inference executor initialized. workers=%s, queue_size=%s, queue_timeout=%ss",
        executor.max_workers,
        executor.queue_size,
        executor.queue_timeout_seconds,
    )
    return executor


def resolve_model_dir(raw_path: str | Path) -> Path:
    """Resolve a configured model path.

    Args:
        raw_path: Absolute path or project-root-relative path from MODEL_PATH.

    Returns:
        The resolved model directory path.
    """
    model_dir = Path(raw_path)
    if model_dir.is_absolute():
        return model_dir

    return PROJECT_ROOT / model_dir


def validate_model_files(model_dir: Path) -> None:
    """Validate that the model directory contains all required model files.

    Args:
        model_dir: Resolved model directory path.

    Raises:
        FileNotFoundError: If any required model file is missing.
    """
    missing_files = [name for name in MODEL_REQUIRED_FILES if not (model_dir / name).exists()]
    if missing_files:
        missing = ", ".join(missing_files)
        raise FileNotFoundError(
            f"Model directory is missing required file(s): {missing}. "
            f"Resolved model directory: {model_dir}"
        )


def load_model(model_path: str | Path | None = None) -> sherpa_onnx.OnlineRecognizer:
    """Loads the Sherpa-onnx OnlineRecognizer with Paraformer model.

    Args:
        model_path: Optional model directory override. Defaults to settings.MODEL_PATH.

    Returns:
        sherpa_onnx.OnlineRecognizer: The loaded recognizer instance.
    """
    model_dir = resolve_model_dir(settings.MODEL_PATH if model_path is None else model_path)
    logging.info(f"Loading Sherpa-onnx model from {model_dir}...")
    
    validate_model_files(model_dir)
    encoder = model_dir / "encoder.int8.onnx"
    decoder = model_dir / "decoder.int8.onnx"
    tokens = model_dir / "tokens.txt"

    recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
        tokens=str(tokens),
        encoder=str(encoder),
        decoder=str(decoder),
        num_threads=4,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
        debug=False,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=3.0,
        rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=300.0,
    )
    
    logging.info("Model loaded successfully.")
    return recognizer

class ASRInferenceService:
    def __init__(
        self,
        recognizer: sherpa_onnx.OnlineRecognizer,
        inference_executor: BoundedInferenceExecutor,
    ):
        self.recognizer = recognizer
        self.stream = self.recognizer.create_stream()
        self.inference_executor = inference_executor

    async def infer(self, audio_input: Any) -> Tuple[str, bool]:
        """Runs inference on the provided audio chunk.

        Args:
            audio_input (Any): The prepared audio samples (numpy array or tensor).
                               Examples: np.ndarray (float32), torch.Tensor.
                               Expected shape: (N,) or (1, N).

        Returns:
            Tuple[str, bool]: A tuple containing:
                - text (str): The transcribed text (partial or final).
                - is_final (bool): Whether the segment is considered complete.
        """
        start_time = time.perf_counter()

        # Input validation / conversion
        if hasattr(audio_input, "numpy"):
            samples = audio_input.numpy()
        elif isinstance(audio_input, np.ndarray):
            samples = audio_input
        else:
            # Fallback/Error - assuming it might be a list or bytes if not careful
            # But AudioProcessor returns torch.Tensor
            raise ValueError(f"Unsupported audio input type: {type(audio_input)}")
            
        # Ensure flat float32 array
        # Optimization: Avoid copy if already flat float32
        if samples.dtype != np.float32 or samples.ndim != 1 or not samples.flags["C_CONTIGUOUS"]:
            samples = samples.flatten().astype(np.float32)
        sample_count = len(samples)

        def _blocking_infer():
            block_start = time.perf_counter()
            self.stream.accept_waveform(16000, samples)
            
            # Decode
            while self.recognizer.is_ready(self.stream):
                self.recognizer.decode_stream(self.stream)
            
            text = self.recognizer.get_result(self.stream)
            is_endpoint = self.recognizer.is_endpoint(self.stream)

            if is_endpoint:
                self.recognizer.reset(self.stream)
            
            block_duration = time.perf_counter() - block_start
            return text, is_endpoint, block_duration

        # Run CPU-bound generation in a separate thread with context propagation
        ctx = contextvars.copy_context()
        text, is_final, cpu_duration = await self.inference_executor.run(
            lambda: ctx.run(_blocking_infer)
        )

        total_duration = time.perf_counter() - start_time

        logging.debug(
            f"[Inference] Samples: {sample_count}, "
            f"CPU Time: {cpu_duration:.6f}s, Total Time: {total_duration:.6f}s. "
            f"Result: {'FINAL' if is_final else 'PARTIAL'}, TextLen: {len(text)}"
        )
        
        return text.strip(), is_final
