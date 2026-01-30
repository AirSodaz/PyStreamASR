import asyncio
import sherpa_onnx
import os
import numpy as np
import logging
import time
import contextvars
from typing import Tuple, Any

# Define model paths relative to project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(PROJECT_ROOT, "models", "sherpa-onnx-streaming-paraformer-bilingual-zh-en")

def load_model() -> sherpa_onnx.OnlineRecognizer:
    """Loads the Sherpa-onnx OnlineRecognizer with Paraformer model.

    Returns:
        sherpa_onnx.OnlineRecognizer: The loaded recognizer instance.
    """
    logging.info(f"Loading Sherpa-onnx model from {MODEL_DIR}...")
    
    encoder = os.path.join(MODEL_DIR, "encoder.int8.onnx")
    decoder = os.path.join(MODEL_DIR, "decoder.int8.onnx")
    tokens = os.path.join(MODEL_DIR, "tokens.txt")

    if not os.path.exists(encoder):
        raise FileNotFoundError(f"Model file not found: {encoder}")

    recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
        tokens=tokens,
        encoder=encoder,
        decoder=decoder,
        num_threads=4,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
        debug=False,
        enable_endpoint_detection=True,
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
