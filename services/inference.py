import asyncio
import sherpa_onnx
import numpy as np
import logging
import time
import contextvars
from pathlib import Path
from typing import Tuple, Any

from core.config import PROJECT_ROOT, settings

MODEL_REQUIRED_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt")


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
    def __init__(self, recognizer: sherpa_onnx.OnlineRecognizer):
        self.recognizer = recognizer
        self.stream = self.recognizer.create_stream()

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
        loop = asyncio.get_running_loop()
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
        text, is_final, cpu_duration = await loop.run_in_executor(None, ctx.run, _blocking_infer)

        total_duration = time.perf_counter() - start_time

        logging.debug(
            f"[Inference] Samples: {sample_count}, "
            f"CPU Time: {cpu_duration:.6f}s, Total Time: {total_duration:.6f}s. "
            f"Result: {'FINAL' if is_final else 'PARTIAL'}, TextLen: {len(text)}"
        )
        
        return text.strip(), is_final
