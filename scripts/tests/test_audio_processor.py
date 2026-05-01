"""Unit tests for audio decoding and resampling boundaries."""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services import audio


class AudioProcessorTests(unittest.TestCase):
    """Test AudioProcessor input and output contracts."""

    def override_attr(self, obj: object, name: str, value: object) -> None:
        """Temporarily replace an attribute."""
        original_value = getattr(obj, name)
        self.addCleanup(setattr, obj, name, original_value)
        setattr(obj, name, value)

    def make_processor(self, input_format: str = "pcm16le", source_rate: int = 16000) -> audio.AudioProcessor:
        """Create an audio processor with patched input settings."""
        self.override_attr(audio.settings, "AUDIO_INPUT_FORMAT", input_format)
        self.override_attr(audio.settings, "AUDIO_SOURCE_RATE", source_rate)
        return audio.AudioProcessor()

    def assert_float32_mono_contiguous(self, samples: np.ndarray) -> None:
        """Assert the standard processed-audio array contract."""
        self.assertEqual(samples.dtype, np.float32)
        self.assertEqual(samples.ndim, 1)
        self.assertTrue(samples.flags.c_contiguous)

    def test_pcm16le_16k_process_returns_normalized_float32(self) -> None:
        """PCM16LE at 16 kHz should normalize samples without changing length."""
        processor = self.make_processor(source_rate=16000)
        pcm16 = np.array([-32768, -16384, 0, 16384, 32767], dtype=np.int16)

        result = processor.process(pcm16.tobytes())

        self.assert_float32_mono_contiguous(result)
        self.assertEqual(result.shape, (5,))
        np.testing.assert_allclose(
            result,
            np.array([-1.0, -0.5, 0.0, 0.5, 32767.0 / 32768.0], dtype=np.float32),
        )

    def test_pcm16le_8k_process_resamples_to_16k_float32(self) -> None:
        """PCM16LE at 8 kHz should resample to the 16 kHz target contract."""
        processor = self.make_processor(source_rate=8000)
        pcm16 = np.array([0, 8192, -8192, 16384], dtype=np.int16)

        result = processor.process(pcm16.tobytes())

        self.assert_float32_mono_contiguous(result)
        self.assertEqual(result.shape, (8,))

    def test_process_empty_chunk_returns_empty_float32_array(self) -> None:
        """Empty chunks should return an empty one-dimensional float32 array."""
        processor = self.make_processor(source_rate=8000)

        result = processor.process(b"")

        self.assert_float32_mono_contiguous(result)
        self.assertEqual(result.shape, (0,))

    def test_pcm16le_odd_length_chunk_raises_value_error(self) -> None:
        """PCM16LE chunks must contain complete int16 samples."""
        processor = self.make_processor(source_rate=16000)

        with self.assertRaises(ValueError):
            processor.process(b"\x00")

    def test_resample_empty_input_returns_empty_float32_array(self) -> None:
        """Resampling empty input should avoid interpolation and return empty float32."""
        processor = self.make_processor(source_rate=8000)

        result = processor.resample(np.array([], dtype=np.float64))

        self.assert_float32_mono_contiguous(result)
        self.assertEqual(result.shape, (0,))

    def test_resample_16k_passthrough_normalizes_dtype_and_contiguity(self) -> None:
        """16 kHz passthrough should still return float32 C-contiguous mono data."""
        processor = self.make_processor(source_rate=16000)
        non_contiguous = np.array([[0.0, 0.25, -0.25, 0.5]], dtype=np.float64)[:, ::2]

        result = processor.resample(non_contiguous)

        self.assert_float32_mono_contiguous(result)
        np.testing.assert_allclose(result, np.array([0.0, -0.25], dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
