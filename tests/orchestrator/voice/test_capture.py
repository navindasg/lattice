"""Tests for lattice.orchestrator.voice.capture — AudioCapture."""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from lattice.orchestrator.voice.capture import AudioCapture


class TestAudioCaptureStart:
    def test_start_resets_frames_and_opens_stream(self) -> None:
        capture = AudioCapture()
        mock_stream = MagicMock()

        with patch("lattice.orchestrator.voice.capture.sd") as mock_sd:
            mock_sd.InputStream.return_value = mock_stream
            capture.start()

        assert capture._frames == []
        mock_sd.InputStream.assert_called_once()
        mock_stream.start.assert_called_once()

    def test_start_creates_stream_with_correct_params(self) -> None:
        capture = AudioCapture()
        mock_stream = MagicMock()

        with patch("lattice.orchestrator.voice.capture.sd") as mock_sd:
            mock_sd.InputStream.return_value = mock_stream
            capture.start()

        call_kwargs = mock_sd.InputStream.call_args.kwargs
        assert call_kwargs["samplerate"] == 16000
        assert call_kwargs["channels"] == 1
        assert call_kwargs["dtype"] == "int16"

    def test_start_raises_runtime_error_on_port_audio_error(self) -> None:
        capture = AudioCapture()

        with patch("lattice.orchestrator.voice.capture.sd") as mock_sd:
            mock_sd.PortAudioError = Exception
            mock_sd.InputStream.side_effect = Exception("No audio device")
            with pytest.raises(RuntimeError, match="No audio input device available"):
                capture.start()


class TestAudioCaptureCallback:
    def test_callback_appends_copy_of_indata(self) -> None:
        capture = AudioCapture()
        capture._frames = []
        indata = np.array([[1, 2, 3]], dtype=np.int16)

        capture._callback(indata, 3, None, None)

        assert len(capture._frames) == 1
        np.testing.assert_array_equal(capture._frames[0], indata)

    def test_callback_appends_copy_not_reference(self) -> None:
        capture = AudioCapture()
        capture._frames = []
        indata = np.array([[1, 2, 3]], dtype=np.int16)

        capture._callback(indata, 3, None, None)
        # Mutate original — frame should not change (copy was stored)
        indata[0, 0] = 999

        assert capture._frames[0][0, 0] != 999

    def test_callback_accumulates_multiple_frames(self) -> None:
        capture = AudioCapture()
        capture._frames = []

        for i in range(5):
            indata = np.array([[i]], dtype=np.int16)
            capture._callback(indata, 1, None, None)

        assert len(capture._frames) == 5


class TestAudioCaptureStop:
    def _make_capture_with_frames(
        self, frame_count: int, samples_per_frame: int = 800
    ) -> AudioCapture:
        """Create capture with pre-filled frames. 800 samples at 16kHz = 50ms per frame."""
        capture = AudioCapture()
        capture._stream = None
        for _ in range(frame_count):
            arr = np.zeros((samples_per_frame, 1), dtype=np.int16)
            capture._frames.append(arr)
        return capture

    def test_stop_returns_none_when_no_frames(self) -> None:
        capture = AudioCapture()
        capture._stream = None
        capture._frames = []
        result = capture.stop()
        assert result is None

    def test_stop_returns_none_when_under_min_duration(self) -> None:
        # 1 frame * 800 samples / 16000 Hz = 50ms < 300ms minimum
        capture = self._make_capture_with_frames(frame_count=1, samples_per_frame=800)
        result = capture.stop()
        assert result is None

    def test_stop_returns_array_for_sufficient_duration(self) -> None:
        # 10 frames * 800 samples / 16000 Hz = 500ms > 300ms minimum
        capture = self._make_capture_with_frames(frame_count=10, samples_per_frame=800)
        result = capture.stop()
        assert result is not None
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.int16

    def test_stop_concatenates_and_flattens_frames(self) -> None:
        capture = self._make_capture_with_frames(frame_count=10, samples_per_frame=800)
        result = capture.stop()
        assert result is not None
        assert result.ndim == 1
        assert len(result) == 8000  # 10 frames * 800 samples

    def test_stop_closes_stream(self) -> None:
        capture = AudioCapture()
        mock_stream = MagicMock()
        capture._stream = mock_stream
        capture._frames = []

        capture.stop()

        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()
        assert capture._stream is None


class TestAudioCaptureToWavBytes:
    def test_to_wav_bytes_produces_valid_wav_header(self) -> None:
        capture = AudioCapture()
        # 500ms of silence at 16kHz
        audio = np.zeros(8000, dtype=np.int16)
        wav_bytes = capture.to_wav_bytes(audio)

        assert wav_bytes[:4] == b"RIFF"

    def test_to_wav_bytes_returns_bytes(self) -> None:
        capture = AudioCapture()
        audio = np.zeros(8000, dtype=np.int16)
        result = capture.to_wav_bytes(audio)
        assert isinstance(result, bytes)
        assert len(result) > 44  # WAV header is 44 bytes minimum
