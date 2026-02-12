import g711
import numpy as np
import logging
import time


class AudioProcessor:
    def __init__(self):
        # Target sample rate
        self.target_rate = 16000
        
        # --- CONFIGURATION (Manual Switch) ---
        # Format: "g711" (default) or "pcm16"
        self.input_format = "g711" 
        # Source Sample Rate: 8000 for G.711, 16000 for typical PCM, etc.
        self.source_rate = 8000  
        # -------------------------------------

    def decode_g711(self, data: bytes) -> np.ndarray:
        """Decodes G.711 A-law bytes to PCM (int16 or float32).

        Args:
            data (bytes): The input G.711 A-law encoded audio bytes.

        Returns:
            np.ndarray: A numpy array of int16 PCM data or float32 data.
        """
        start_time = time.perf_counter()

        # g711.decode_alaw returns bytes representing int16 PCM (in older versions)
        # or a numpy array of float32 (in newer versions like 1.6.5)
        decoded = g711.decode_alaw(data)

        if isinstance(decoded, np.ndarray):
            result = decoded
        else:
            # Convert bytes to numpy array of int16
            result = np.frombuffer(decoded, dtype=np.int16)

        duration = time.perf_counter() - start_time
        logging.debug(f"[Audio] decode_g711 took {duration:.6f}s. Output shape: {result.shape}, dtype: {result.dtype}")
        return result

    def resample(self, pcm_data: np.ndarray) -> np.ndarray:
        """Resamples source_rate PCM data to target_rate using linear interpolation.

        Args:
            pcm_data (np.ndarray): The input PCM data as a numpy array of float32.

        Returns:
            np.ndarray: The resampled waveform as a float32 array.
        """
        start_time = time.perf_counter()
        if self.source_rate == self.target_rate:
            return pcm_data

        duration_sec = len(pcm_data) / self.source_rate
        target_len = int(duration_sec * self.target_rate)

        # Create time points for input and output
        x_old = np.linspace(0, duration_sec, len(pcm_data))
        x_new = np.linspace(0, duration_sec, target_len)
        
        # Linear interpolation
        resampled = np.interp(x_new, x_old, pcm_data)
        
        result = resampled.astype(np.float32)

        duration = time.perf_counter() - start_time
        logging.debug(f"[Audio] resample took {duration:.6f}s. New shape: {result.shape}")
        return result

    def process(self, chunk: bytes) -> np.ndarray:
        """Full pipeline: Decode (G.711/PCM) -> Normalize -> Resample.

        Args:
            chunk (bytes): The input audio chunk.

        Returns:
            np.ndarray: A Float32 array normalized to the [-1, 1] range.
        """
        process_start = time.perf_counter()
        input_len = len(chunk)
        logging.debug(f"[Audio] Processing chunk of size {input_len} bytes, fmt={self.input_format}")

        # 1. Decode / Load
        if self.input_format == "pcm16":
            # Input is raw Int16 PCM bytes
            pcm_data = np.frombuffer(chunk, dtype=np.int16)
        else:
            # Default to G.711 A-law
            pcm_data = self.decode_g711(chunk)

        # 2. Normalize to [-1, 1]
        # Whether from G.711 decode (if int16) or direct PCM16 load, we normalise here.
        if pcm_data.dtype == np.int16:
            pcm_data = pcm_data.astype(np.float32) / 32768.0

        # 3. Resample
        result = self.resample(pcm_data)

        process_duration = time.perf_counter() - process_start
        logging.debug(f"[Audio] Total process took {process_duration:.6f}s")

        return result
