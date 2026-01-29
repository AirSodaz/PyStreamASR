import g711
import numpy as np
import torch
import torchaudio


class AudioProcessor:
    def __init__(self):
        # Initialize resampler: 8000Hz -> 16000Hz
        self.resampler = torchaudio.transforms.Resample(orig_freq=8000, new_freq=16000)

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

    def resample(self, pcm_data: np.ndarray) -> torch.Tensor:
        """Resamples 8000Hz PCM data to 16000Hz.

        Args:
            pcm_data (np.ndarray): The input PCM data as a numpy array of float32.

        Returns:
            torch.Tensor: The resampled waveform as a float32 tensor.
        """
        # Convert numpy array to torch tensor
        waveform = torch.from_numpy(pcm_data)

        # Resampler expects (channel, time) or (time)
        resampled_waveform = self.resampler(waveform)
        return resampled_waveform

    def process(self, chunk: bytes) -> torch.Tensor:
        """Full pipeline: Decode G.711 -> Normalize -> Resample.

        Args:
            chunk (bytes): The input audio chunk in G.711 A-law format.

        Returns:
            torch.Tensor: A Float32 Tensor normalized to the [-1, 1] range.
        """
        # 1. Decode
        pcm_data = self.decode_g711(chunk)

        # 2. Normalize to [-1, 1] only if it was integer data
        # If g711 returned float array, it is already normalized [-1, 1]
        if pcm_data.dtype == np.int16:
            pcm_data = pcm_data.astype(np.float32) / 32768.0

        # 3. Resample using the class method
        return self.resample(pcm_data)
