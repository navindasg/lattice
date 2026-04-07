"""Speech-to-text provider with local faster-whisper and Deepgram cloud fallback.

Primary: WhisperModel (faster-whisper) with lazy loading — first load takes
2-3s, subsequent calls are fast since the model stays in memory.

Fallback: DeepgramFallback activates when avg_logprob < threshold OR the
local transcript is empty. Audio only leaves the local machine if local STT
fails. If Deepgram is not configured (empty api_key), local transcript is
returned even if confidence is low.

See RESEARCH.md Pitfall 4 for the lazy-iterator issue with faster-whisper.
"""
from __future__ import annotations

import structlog

import numpy as np

logger = structlog.get_logger(__name__)

# Lazy import — avoid import error when faster-whisper not installed
try:
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    WhisperModel = None  # type: ignore[assignment,misc]


# Lazy import for Deepgram — avoid import error when deepgram-sdk not installed
try:
    from deepgram import DeepgramClient  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    DeepgramClient = None  # type: ignore[assignment,misc]


class DeepgramFallback:
    """Cloud STT fallback via Deepgram nova-3.

    Audio is only sent to Deepgram when:
    1. api_key is non-empty (configured), AND
    2. Local STT returns low confidence (avg_logprob < threshold) or empty transcript

    If api_key is empty, transcribe() returns "" immediately without network calls.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client: object | None = None

    def transcribe(self, wav_bytes: bytes) -> str:
        """Transcribe WAV bytes via Deepgram nova-3.

        Returns:
            Transcription string, or "" if not configured or on any error.
        """
        if not self._api_key:
            return ""

        try:
            if self._client is None:
                self._client = DeepgramClient(self._api_key)

            response = self._client.listen.v1.media.transcribe_file(  # type: ignore[union-attr]
                request=wav_bytes,
                model="nova-3",
            )
            return response.results.channels[0].alternatives[0].transcript
        except Exception as exc:
            logger.warning("deepgram_transcription_failed", error=str(exc))
            return ""


class STTProvider:
    """Local STT with faster-whisper and Deepgram cloud fallback.

    The WhisperModel is loaded lazily on the first call to _ensure_loaded()
    and kept in memory for subsequent calls. This avoids 2-3s startup latency
    on every command after the first.

    Args:
        model_size: faster-whisper model identifier, e.g. "small.en"
        deepgram_api_key: API key for Deepgram fallback. Leave empty to disable.
    """

    def __init__(self, model_size: str = "small.en", deepgram_api_key: str = "") -> None:
        self._model_size = model_size
        self._model: object | None = None
        self._deepgram = DeepgramFallback(deepgram_api_key)

    def _ensure_loaded(self) -> object:
        """Lazy-load WhisperModel on first call, return cached instance thereafter."""
        if self._model is None:
            self._model = WhisperModel(
                self._model_size,
                device="cpu",
                compute_type="int8",
            )
            logger.info("whisper_model_loaded", model_size=self._model_size)
        return self._model

    def transcribe(self, audio_np: np.ndarray, sample_rate: int = 16000) -> tuple[str, float]:
        """Transcribe audio using local faster-whisper.

        CRITICAL: all_segs = list(segments) is required to consume the lazy
        generator returned by model.transcribe(). Partial consumption results
        in truncated transcripts (see RESEARCH.md Pitfall 4).

        Args:
            audio_np: Flat int16 numpy array at sample_rate Hz.
            sample_rate: Sample rate of audio_np (default 16000).

        Returns:
            Tuple of (transcript_string, avg_logprob). When no segments are
            returned, avg_logprob is -1.0 (worst possible confidence).
        """
        model = self._ensure_loaded()
        # VAD filter requires float32 normalized to [-1, 1]
        audio_f32 = audio_np.astype(np.float32) / 32768.0
        segments, _info = model.transcribe(audio_f32, language="en", beam_size=5, vad_filter=True)  # type: ignore[union-attr]

        # CRITICAL: consume the lazy generator before processing
        all_segs = list(segments)

        texts = [seg.text.strip() for seg in all_segs]
        logprobs = [seg.avg_logprob for seg in all_segs]

        transcript = " ".join(t for t in texts if t)
        avg_logprob = sum(logprobs) / len(logprobs) if logprobs else -1.0

        return transcript, avg_logprob

    def transcribe_with_fallback(
        self,
        audio_np: np.ndarray,
        wav_bytes: bytes,
        threshold: float = -0.6,
    ) -> str:
        """Transcribe audio with automatic Deepgram fallback on low confidence.

        Fallback triggers when:
        - transcript is non-empty AND avg_logprob >= threshold → return local transcript
        - transcript is empty OR avg_logprob < threshold → attempt Deepgram fallback

        When Deepgram returns empty (not configured or error), the local transcript
        is returned as-is even if confidence is low.

        Args:
            audio_np: Flat int16 numpy array for local STT.
            wav_bytes: WAV-encoded bytes for Deepgram (pre-built by caller).
            threshold: avg_logprob below this value triggers fallback (default -0.6).

        Returns:
            Best available transcription string.
        """
        transcript, avg_logprob = self.transcribe(audio_np)

        if transcript and avg_logprob >= threshold:
            return transcript

        logger.info(
            "stt_fallback_triggered",
            avg_logprob=avg_logprob,
            transcript_empty=(not transcript),
        )

        fallback_result = self._deepgram.transcribe(wav_bytes)
        return fallback_result if fallback_result else transcript
