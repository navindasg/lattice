"""Audio capture module for push-to-talk voice input.

Uses sounddevice InputStream to accumulate 16kHz mono int16 audio frames
while the hotkey is held. On release, assembles frames into a numpy array
and validates minimum recording duration.

AudioCapture is intentionally stateless between start/stop cycles — each
push-to-talk creates a fresh recording.
"""
from __future__ import annotations

import io

import numpy as np
import structlog
from scipy.io import wavfile

logger = structlog.get_logger(__name__)

# Lazy-load sounddevice to avoid PortAudio OSError in environments
# without audio hardware (CI, containers). The module-level `sd` name
# is set on first use so existing patch targets (capture.sd) keep working.
sd: object | None = None


def _ensure_sd():  # noqa: ANN202
    global sd  # noqa: PLW0603
    if sd is None:
        import sounddevice as _sd
        sd = _sd
    return sd


def check_microphone() -> bool:
    """Check whether an audio input device is available.

    Returns True if at least one input device exists, False otherwise.
    Logs the detected device name on success.
    """
    try:
        _sd = _ensure_sd()
        device_info = _sd.query_devices(kind="input")
        logger.info(
            "microphone_detected",
            device=device_info.get("name", "unknown"),
            sample_rate=device_info.get("default_samplerate"),
        )
        return True
    except Exception as exc:
        logger.warning("no_microphone_detected", error=str(exc))
        return False


class AudioCapture:
    """Captures 16kHz mono int16 audio from the default input device.

    Typical use:
        capture = AudioCapture()
        capture.start()           # hotkey press
        audio = capture.stop()    # hotkey release
        if audio is not None:
            wav = capture.to_wav_bytes(audio)
    """

    SAMPLE_RATE: int = 16000
    CHANNELS: int = 1
    DTYPE: str = "int16"
    MIN_DURATION_MS: int = 300

    def __init__(self) -> None:
        self._frames: list[np.ndarray] = []
        self._stream: object | None = None

    def start(self) -> None:
        """Reset accumulated frames and open a new InputStream.

        Raises:
            RuntimeError: If no audio input device is available (PortAudioError).
        """
        _sd = _ensure_sd()
        self._frames = []
        try:
            self._stream = _sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                callback=self._callback,
            )
            self._stream.start()
        except _sd.PortAudioError:
            logger.error("audio_device_unavailable", detail="No audio input device available")
            raise RuntimeError("No audio input device available")

    def _callback(self, indata: np.ndarray, frames: int, time: object, status: object) -> None:
        """PortAudio callback — append a copy of the current buffer.

        The copy is critical: PortAudio reuses the indata buffer on every callback.
        Without copying, all frames would alias the same final buffer contents.
        """
        self._frames.append(indata.copy())

    def stop(self) -> np.ndarray | None:
        """Stop recording and return the assembled audio array.

        Returns:
            A flat int16 numpy array of shape (N,) if the recording meets
            the minimum duration requirement, otherwise None.
            None is also returned when no frames were captured.
        """
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._frames:
            return None

        audio = np.concatenate(self._frames, axis=0).flatten()
        duration_ms = (len(audio) / self.SAMPLE_RATE) * 1000
        if duration_ms < self.MIN_DURATION_MS:
            return None

        return audio

    def to_wav_bytes(self, audio: np.ndarray) -> bytes:
        """Encode a numpy int16 array as a WAV file in memory.

        Args:
            audio: Flat int16 numpy array at SAMPLE_RATE.

        Returns:
            WAV-encoded bytes starting with b"RIFF".
        """
        buf = io.BytesIO()
        wavfile.write(buf, self.SAMPLE_RATE, audio)
        return buf.getvalue()
