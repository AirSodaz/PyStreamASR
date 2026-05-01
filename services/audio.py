import logging
from datetime import datetime, timezone
from pathlib import Path
import re
import time
import wave

import g711
import numpy as np

from core.config import settings


def is_debug_audio_enabled() -> bool:
    """Return whether debug-audio capture should be enabled."""
    return settings.LOG_LEVEL.strip().upper() == "DEBUG"


def _sanitize_file_component(value: str) -> str:
    """Convert arbitrary session IDs into safe filename components.

    Args:
        value: Raw session identifier.

    Returns:
        A filesystem-safe string suitable for a filename stem.
    """
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return sanitized or "session"


class DebugAudioWriter:
    """Write processed audio samples to a WAV file for debug inspection."""

    def __init__(self, session_id: str, log_dir: str, sample_rate: int = 16000) -> None:
        """Initialize a per-connection WAV writer.

        Args:
            session_id: Session identifier associated with the connection.
            log_dir: Base log directory from application settings.
            sample_rate: WAV sample rate in hertz.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        debug_dir = Path(log_dir) / "debug_audio"
        debug_dir.mkdir(parents=True, exist_ok=True)

        safe_session_id = _sanitize_file_component(session_id)
        self.path = debug_dir / f"{safe_session_id}_{timestamp}.wav"
        self._wave_file = wave.open(str(self.path), "wb")
        self._wave_file.setnchannels(1)
        self._wave_file.setsampwidth(2)
        self._wave_file.setframerate(sample_rate)
        self._closed = False

        logging.debug(f"[Audio] Debug audio writer initialized: {self.path}")

    def append_samples(self, samples: np.ndarray) -> None:
        """Append normalized float32 samples to the debug WAV file.

        Args:
            samples: Mono audio samples in the [-1, 1] range.
        """
        if self._closed:
            raise RuntimeError("Cannot append to a closed debug audio writer.")

        sample_array = np.asarray(samples, dtype=np.float32)
        clipped = np.clip(sample_array, -1.0, 1.0)
        pcm16 = np.where(
            clipped >= 0.0,
            np.rint(clipped * 32767.0),
            np.rint(clipped * 32768.0),
        ).astype(np.int16)
        self._wave_file.writeframes(pcm16.tobytes())

    def close(self) -> None:
        """Close the underlying WAV file."""
        if self._closed:
            return

        self._wave_file.close()
        self._closed = True
        logging.debug(f"[Audio] Debug audio writer closed: {self.path}")


def create_debug_audio_writer(session_id: str) -> DebugAudioWriter | None:
    """Create a debug-audio writer when debug logging is enabled.

    Args:
        session_id: Session identifier associated with the connection.

    Returns:
        A configured writer, or ``None`` when debug capture is disabled.
    """
    if not is_debug_audio_enabled():
        return None

    return DebugAudioWriter(session_id=session_id, log_dir=settings.LOG_DIR)


def close_debug_audio_writer(writer: DebugAudioWriter | None) -> None:
    """Close a debug-audio writer if it exists.

    Args:
        writer: The writer to close.
    """
    if writer is None:
        return

    writer.close()


def append_debug_audio_samples(
    writer: DebugAudioWriter | None,
    session_id: str,
    samples: np.ndarray,
) -> DebugAudioWriter | None:
    """Create or append a debug audio artifact and fail closed.

    Args:
        writer: Existing per-connection writer, if any.
        session_id: Session identifier for file naming.
        samples: Processed audio samples ready for ASR.

    Returns:
        The active writer instance, or ``None`` when capture is disabled or failed.
    """
    try:
        if writer is None:
            writer = create_debug_audio_writer(session_id)

        if writer is None:
            return None

        writer.append_samples(samples)
        return writer
    except Exception as exc:
        logging.error(f"[Audio] Debug audio capture failed for session {session_id}: {exc}")
        try:
            close_debug_audio_writer(writer)
        except Exception as close_exc:
            logging.error(f"[Audio] Failed to close debug audio writer: {close_exc}")
        return None


def _as_float32_mono_contiguous(samples: np.ndarray) -> np.ndarray:
    """Normalize audio arrays to the ASR boundary contract.

    Args:
        samples: Input audio samples.

    Returns:
        A one-dimensional C-contiguous float32 array.
    """
    sample_array = np.asarray(samples, dtype=np.float32).reshape(-1)
    return np.ascontiguousarray(sample_array, dtype=np.float32)


class AudioProcessor:
    def __init__(self):
        # Target sample rate
        self.target_rate = 16000
        
        # Configuration from settings
        self.input_format = settings.AUDIO_INPUT_FORMAT.strip().lower()
        self.source_rate = int(settings.AUDIO_SOURCE_RATE)

        supported_formats = {"alaw", "ulaw", "pcm16le"}
        if self.input_format not in supported_formats:
            raise ValueError(
                f"Unsupported AUDIO_INPUT_FORMAT: {self.input_format}. "
                f"Supported: {', '.join(sorted(supported_formats))}"
            )

        if self.source_rate not in (8000, 16000):
            raise ValueError(
                f"Unsupported AUDIO_SOURCE_RATE: {self.source_rate}. "
                "Supported: 8000 or 16000"
            )

        # Resolve decoder function once to avoid dynamic lookup in the hot loop.
        # Default to A-law if not PCM16LE or Mu-law, matching original behavior.
        self._decoder = None
        self._decoder_name = ""
        if self.input_format != "pcm16le":
            if self.input_format == "ulaw":
                self._decoder_name = "decode_ulaw"
            else:
                self._decoder_name = "decode_alaw"

            self._decoder = getattr(g711, self._decoder_name, None)
            if self._decoder is None:
                raise RuntimeError(
                    f"g711.{self._decoder_name} is not available. "
                    "Please update the g711 package."
                )

    def _decode_g711(self, data: bytes) -> np.ndarray:
        """Decodes G.711 bytes to PCM (int16 or float32).

        Args:
            data (bytes): The input G.711 encoded audio bytes.

        Returns:
            np.ndarray: A numpy array of int16 PCM data or float32 data.
        """
        start_time = time.perf_counter()

        # g711.decode_* returns bytes representing int16 PCM (in older versions)
        # or a numpy array of float32 (in newer versions like 1.6.5)
        decoded = self._decoder(data)

        if isinstance(decoded, np.ndarray):
            result = decoded
        else:
            # Convert bytes to numpy array of int16
            result = np.frombuffer(decoded, dtype=np.int16)

        duration = time.perf_counter() - start_time
        logging.debug(
            f"[Audio] {self._decoder_name} took {duration:.6f}s. "
            f"Output shape: {result.shape}, dtype: {result.dtype}"
        )
        return result

    def resample(self, pcm_data: np.ndarray) -> np.ndarray:
        """Resamples source_rate PCM data to target_rate using linear interpolation.

        Args:
            pcm_data (np.ndarray): The input PCM data as a numpy array of float32.

        Returns:
            np.ndarray: The resampled waveform as a float32 array.
        """
        start_time = time.perf_counter()
        pcm_samples = _as_float32_mono_contiguous(pcm_data)
        if pcm_samples.size == 0:
            return pcm_samples

        if self.source_rate == self.target_rate:
            return pcm_samples

        duration_sec = len(pcm_samples) / self.source_rate
        target_len = int(duration_sec * self.target_rate)

        # Create time points for input and output
        x_old = np.linspace(0, duration_sec, len(pcm_samples))
        x_new = np.linspace(0, duration_sec, target_len)
        
        # Linear interpolation
        resampled = np.interp(x_new, x_old, pcm_samples)
        
        result = _as_float32_mono_contiguous(resampled)

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
        if self.input_format == "pcm16le":
            if input_len % np.dtype(np.int16).itemsize != 0:
                raise ValueError("PCM16LE chunks must contain an even number of bytes.")

            # Input is raw Int16 PCM bytes
            pcm_data = np.frombuffer(chunk, dtype=np.int16)
        else:
            # G.711 mu-law or A-law
            pcm_data = self._decode_g711(chunk)

        # 2. Normalize to [-1, 1]
        # Whether from G.711 decode (if int16) or direct PCM16 load, we normalise here.
        if pcm_data.dtype == np.int16:
            pcm_data = pcm_data.astype(np.float32) / 32768.0

        # 3. Resample
        result = self.resample(pcm_data)

        process_duration = time.perf_counter() - process_start
        logging.debug(f"[Audio] Total process took {process_duration:.6f}s")

        return _as_float32_mono_contiguous(result)
