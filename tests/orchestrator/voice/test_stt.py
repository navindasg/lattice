"""Tests for lattice.orchestrator.voice.stt — STTProvider and DeepgramFallback."""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from lattice.orchestrator.voice.stt import DeepgramFallback, STTProvider


def _make_mock_segment(text: str, avg_logprob: float) -> MagicMock:
    seg = MagicMock()
    seg.text = text
    seg.avg_logprob = avg_logprob
    return seg


class TestSTTProviderEnsureLoaded:
    def test_ensure_loaded_creates_model_on_first_call(self) -> None:
        provider = STTProvider(model_size="tiny.en")

        with patch("lattice.orchestrator.voice.stt.WhisperModel") as mock_cls:
            mock_model = MagicMock()
            mock_cls.return_value = mock_model

            result = provider._ensure_loaded()

            mock_cls.assert_called_once_with("tiny.en", device="cpu", compute_type="int8")
            assert result is mock_model

    def test_ensure_loaded_reuses_model_on_second_call(self) -> None:
        provider = STTProvider()

        with patch("lattice.orchestrator.voice.stt.WhisperModel") as mock_cls:
            mock_model = MagicMock()
            mock_cls.return_value = mock_model

            provider._ensure_loaded()
            provider._ensure_loaded()

            assert mock_cls.call_count == 1


class TestSTTProviderTranscribe:
    def test_transcribe_returns_transcript_and_logprob(self) -> None:
        provider = STTProvider()
        audio = np.zeros(8000, dtype=np.int16)

        seg1 = _make_mock_segment("hello world", -0.3)
        seg2 = _make_mock_segment("how are you", -0.4)

        with patch("lattice.orchestrator.voice.stt.WhisperModel") as mock_cls:
            mock_model = MagicMock()
            mock_model.transcribe.return_value = (iter([seg1, seg2]), MagicMock())
            mock_cls.return_value = mock_model

            transcript, avg_logprob = provider.transcribe(audio)

        assert transcript == "hello world how are you"
        assert abs(avg_logprob - (-0.35)) < 0.001

    def test_transcribe_returns_empty_when_no_segments(self) -> None:
        provider = STTProvider()
        audio = np.zeros(8000, dtype=np.int16)

        with patch("lattice.orchestrator.voice.stt.WhisperModel") as mock_cls:
            mock_model = MagicMock()
            mock_model.transcribe.return_value = (iter([]), MagicMock())
            mock_cls.return_value = mock_model

            transcript, avg_logprob = provider.transcribe(audio)

        assert transcript == ""
        assert avg_logprob == -1.0

    def test_transcribe_strips_segment_text(self) -> None:
        provider = STTProvider()
        audio = np.zeros(8000, dtype=np.int16)

        seg = _make_mock_segment("  map the auth  ", -0.3)

        with patch("lattice.orchestrator.voice.stt.WhisperModel") as mock_cls:
            mock_model = MagicMock()
            mock_model.transcribe.return_value = (iter([seg]), MagicMock())
            mock_cls.return_value = mock_model

            transcript, _ = provider.transcribe(audio)

        assert transcript == "map the auth"


class TestSTTProviderTranscribeWithFallback:
    def _make_provider_with_mock_transcribe(
        self, transcript: str, avg_logprob: float
    ) -> STTProvider:
        provider = STTProvider()
        mock_model = MagicMock()
        seg = _make_mock_segment(transcript, avg_logprob)
        mock_model.transcribe.return_value = (iter([seg]), MagicMock())
        provider._model = mock_model
        return provider

    def test_returns_local_when_confidence_high(self) -> None:
        provider = self._make_provider_with_mock_transcribe("start working", -0.3)
        wav_bytes = b"dummy_wav"

        result = provider.transcribe_with_fallback(
            np.zeros(8000, dtype=np.int16), wav_bytes, threshold=-0.6
        )

        assert result == "start working"

    def test_calls_deepgram_when_logprob_below_threshold(self) -> None:
        provider = self._make_provider_with_mock_transcribe("bad transcript", -0.8)
        provider._deepgram = MagicMock()
        provider._deepgram.transcribe.return_value = "deepgram result"

        result = provider.transcribe_with_fallback(
            np.zeros(8000, dtype=np.int16), b"wav", threshold=-0.6
        )

        provider._deepgram.transcribe.assert_called_once_with(b"wav")
        assert result == "deepgram result"

    def test_calls_deepgram_when_transcript_empty(self) -> None:
        provider = self._make_provider_with_mock_transcribe("", -0.2)
        provider._deepgram = MagicMock()
        provider._deepgram.transcribe.return_value = "deepgram fallback"

        result = provider.transcribe_with_fallback(
            np.zeros(8000, dtype=np.int16), b"wav", threshold=-0.6
        )

        provider._deepgram.transcribe.assert_called_once()
        assert result == "deepgram fallback"

    def test_does_not_call_deepgram_when_logprob_above_threshold(self) -> None:
        provider = self._make_provider_with_mock_transcribe("good transcript", -0.3)
        provider._deepgram = MagicMock()

        provider.transcribe_with_fallback(
            np.zeros(8000, dtype=np.int16), b"wav", threshold=-0.6
        )

        provider._deepgram.transcribe.assert_not_called()

    def test_returns_local_when_deepgram_not_configured(self) -> None:
        provider = self._make_provider_with_mock_transcribe("bad transcript", -0.9)
        provider._deepgram = MagicMock()
        provider._deepgram.transcribe.return_value = ""  # not configured returns empty

        result = provider.transcribe_with_fallback(
            np.zeros(8000, dtype=np.int16), b"wav", threshold=-0.6
        )

        # When deepgram returns empty, should keep local transcript
        assert result == "bad transcript"


class TestDeepgramFallback:
    def test_transcribe_returns_empty_when_no_api_key(self) -> None:
        fallback = DeepgramFallback(api_key="")
        result = fallback.transcribe(b"dummy_wav")
        assert result == ""

    def test_transcribe_calls_deepgram_client_with_api_key(self) -> None:
        fallback = DeepgramFallback(api_key="test-key-123")
        wav_bytes = b"riff_wav_data"

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.results.channels[0].alternatives[0].transcript = "test transcript"
        mock_client.listen.v1.media.transcribe_file.return_value = mock_response

        with patch("lattice.orchestrator.voice.stt.DeepgramClient", return_value=mock_client):
            result = fallback.transcribe(wav_bytes)

        assert result == "test transcript"

    def test_transcribe_returns_empty_on_exception(self) -> None:
        fallback = DeepgramFallback(api_key="test-key-123")

        with patch("lattice.orchestrator.voice.stt.DeepgramClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.listen.v1.media.transcribe_file.side_effect = Exception("API error")
            mock_cls.return_value = mock_client

            result = fallback.transcribe(b"wav_bytes")

        assert result == ""
