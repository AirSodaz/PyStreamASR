import g711
import numpy as np
import torch
import torchaudio

class AudioProcessor:
    def __init__(self):
        # Initialize resampler: 8000Hz -> 16000Hz
        self.resampler = torchaudio.transforms.Resample(orig_freq=8000, new_freq=16000)

    def decode_g711(self, data: bytes) -> np.ndarray:
        """
        Decodes G.711 A-law bytes to PCM 16-bit integers.
        Returns a numpy array of int16.
        """
        # g711.decode_alaw returns bytes representing int16 PCM
        pcm_bytes = g711.decode_alaw(data)
        # Convert bytes to numpy array of int16
        return np.frombuffer(pcm_bytes, dtype=np.int16)

    def resample(self, pcm_data: np.ndarray) -> torch.Tensor:
        """
        Resamples 8000Hz PCM data to 16000Hz.
        Input: numpy array (int16)
        Output: torch Tensor (float32)
        """
        # Convert numpy array to torch tensor and normalize to float32
        waveform = torch.from_numpy(pcm_data).float()
        
        # Resampler expects (channel, time) or (time)
        # We'll assume mono channel for now. 
        # If input is 1D (time), resampler works.
        
        resampled_waveform = self.resampler(waveform)
        return resampled_waveform

    def process(self, chunk: bytes) -> torch.Tensor:
        """
        Full pipeline: Decode G.711 -> Resample -> Normalize.
        Returns a Float32 Tensor normalized to [-1, 1] range (implied by float conversion usually, 
        but we might need explicit normalization if FunASR expects it).
        
        Note: int16 to float conversion in torch preserves values (e.g. 32767.0).
        FunASR/Kaldi often expects input roughly in range [-32768, 32767] or normalized.
        Let's check standard practice. 
        However, standard audio loading often normalizes to [-1, 1]. 
        Let's stick to returning the values as float first. 
        Wait, user requirement says: "Return: A Float32 Numpy array or Tensor normalized to [-1, 1] range".
        """
        # 1. Decode
        pcm_int16 = self.decode_g711(chunk)
        
        # 2. Resample
        # Convert to float and normalize to [-1, 1] BEFORE resampling for better precision? 
        # Or resample then normalize? 
        # Usually converting int16 to float [-1, 1] is done by dividing by 32768.0.
        
        waveform = torch.from_numpy(pcm_int16).float() / 32768.0
        
        resampled = self.resampler(waveform)
        
        return resampled
