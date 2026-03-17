"""Unit tests for debug-audio capture behavior."""

import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services import audio


class FailingWriter:
    """Writer test double that fails during append."""

    def append_samples(self, samples: np.ndarray) -> None:
        """Raise an error to simulate a write failure."""
        raise OSError("disk full")


class DebugAudioWriterTests(unittest.TestCase):
    """Test debug audio capture behavior."""

    def setUp(self) -> None:
        """Create an isolated log directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.logs_dir = Path(self.temp_dir.name) / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def override_attr(self, obj: object, name: str, value: object) -> None:
        """Temporarily replace an attribute."""
        original_value = getattr(obj, name)
        self.addCleanup(setattr, obj, name, original_value)
        setattr(obj, name, value)

    def patch_setting(self, name: str, value: object) -> None:
        """Patch a runtime setting for the duration of the test."""
        self.override_attr(audio.settings, name, value)

    def test_create_debug_audio_writer_disabled_returns_none(self) -> None:
        """No debug artifact should be created outside DEBUG logging."""
        self.patch_setting("LOG_DIR", str(self.logs_dir))
        self.patch_setting("LOG_LEVEL", "INFO")

        writer = audio.create_debug_audio_writer("session-1")

        self.assertIsNone(writer)
        debug_dir = self.logs_dir / "debug_audio"
        self.assertFalse(debug_dir.exists())

    def test_debug_audio_writer_writes_expected_wav(self) -> None:
        """Saved WAV files should use the expected PCM format and clipping."""
        self.patch_setting("LOG_DIR", str(self.logs_dir))
        self.patch_setting("LOG_LEVEL", "DEBUG")

        writer = audio.create_debug_audio_writer("session-1")
        self.assertIsNotNone(writer)

        samples = np.array([-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5], dtype=np.float32)
        writer.append_samples(samples)
        writer.close()

        with wave.open(str(writer.path), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getsampwidth(), 2)
            frame_data = wav_file.readframes(wav_file.getnframes())

        pcm_values = np.frombuffer(frame_data, dtype=np.int16)
        expected = np.array(
            [-32768, -32768, -16384, 0, 16384, 32767, 32767],
            dtype=np.int16,
        )
        np.testing.assert_array_equal(pcm_values, expected)

    def test_debug_audio_writer_uses_unique_sanitized_paths(self) -> None:
        """Writer filenames should be unique and safe for the filesystem."""
        self.patch_setting("LOG_DIR", str(self.logs_dir))
        self.patch_setting("LOG_LEVEL", "DEBUG")

        first = audio.create_debug_audio_writer("session:one/test")
        second = audio.create_debug_audio_writer("session:one/test")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)

        first.close()
        second.close()

        self.assertNotEqual(first.path, second.path)
        self.assertIn("session_one_test", first.path.name)
        self.assertIn("session_one_test", second.path.name)

    def test_append_debug_audio_failure_is_non_fatal(self) -> None:
        """Audio helper should swallow writer failures and continue."""
        samples = np.array([0.1, -0.1], dtype=np.float32)
        close_calls: list[object] = []

        self.override_attr(audio, "create_debug_audio_writer", lambda session_id: FailingWriter())
        self.override_attr(audio, "close_debug_audio_writer", lambda writer: close_calls.append(writer))

        result = audio.append_debug_audio_samples(
            writer=None,
            session_id="session-1",
            samples=samples,
        )

        self.assertIsNone(result)
        self.assertEqual(len(close_calls), 1)


if __name__ == "__main__":
    unittest.main()
