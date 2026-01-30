import g711
import numpy as np


class AudioProcessor:
    def __init__(self):
        # Target sample rate
        self.target_rate = 16000
        self.source_rate = 8000

    def decode_g711(self, data: bytes) -> np.ndarray:
        """Decodes G.711 A-law bytes to PCM (int16 or float32).

        Args:
            data (bytes): The input G.711 A-law encoded audio bytes.

        Returns:
            np.ndarray: A numpy array of int16 PCM data or float32 data.
        """
        # g711.decode_alaw returns bytes representing int16 PCM (in older versions)
        # or a numpy array of float32 (in newer versions like 1.6.5)
        decoded = g711.decode_alaw(data)

        if isinstance(decoded, np.ndarray):
            return decoded

        # Convert bytes to numpy array of int16
        return np.frombuffer(decoded, dtype=np.int16)

    def resample(self, pcm_data: np.ndarray) -> np.ndarray:
        """Resamples 8000Hz PCM data to 16000Hz using linear interpolation.

        Args:
            pcm_data (np.ndarray): The input PCM data as a numpy array of float32.

        Returns:
            np.ndarray: The resampled waveform as a float32 array.
        """
        if self.source_rate == self.target_rate:
            return pcm_data

        duration_sec = len(pcm_data) / self.source_rate
        target_len = int(duration_sec * self.target_rate)

        # Create time points for input and output
        x_old = np.linspace(0, duration_sec, len(pcm_data))
        x_new = np.linspace(0, duration_sec, target_len)
        
        # Linear interpolation
        resampled = np.interp(x_new, x_old, pcm_data)
        
        return resampled.astype(np.float32)

    def process(self, chunk: bytes) -> np.ndarray:
        """Full pipeline: Decode G.711 -> Normalize -> Resample.

        Args:
            chunk (bytes): The input audio chunk in G.711 A-law format.

        Returns:
            np.ndarray: A Float32 array normalized to the [-1, 1] range.
        """
        # 1. Decode
        pcm_data = self.decode_g711(chunk)

        # 2. Normalize to [-1, 1] only if it was integer data
        # If g711 returned float array, it is already normalized [-1, 1]
        if pcm_data.dtype == np.int16:
            pcm_data = pcm_data.astype(np.float32) / 32768.0

        # 3. Resample
        return self.resample(pcm_data)
