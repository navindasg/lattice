"""Voice pipeline data models: VoiceConfig and IntentResult.

All models are frozen Pydantic models (immutable after construction).

VoiceConfig holds push-to-talk pipeline configuration loaded from lattice.yaml.
IntentResult carries the classified intent of a transcribed utterance.
IntentCategory is the Literal type for the 6 possible intent categories.
"""
from typing import Literal

from pydantic import BaseModel


IntentCategory = Literal[
    "task_dispatch",
    "context_injection",
    "status_query",
    "external_fetch",
    "mapper_command",
    "unrecognized",
]


class VoiceConfig(BaseModel):
    """Configuration for the voice capture-to-intent pipeline.

    All fields have safe defaults so VoiceConfig() works out of the box.
    The hotkey defaults to <f13> (F13 key) — hardware Fn key is not
    detectable by pynput on macOS (see RESEARCH.md Pitfall 1).

    Fields:
        hotkey: pynput key name string, e.g. "<f13>" or "<f14>"
        model_size: faster-whisper model identifier, e.g. "small.en"
        confidence_threshold: avg_logprob below this value triggers
            Deepgram cloud fallback (default -0.6)
        min_duration_ms: recordings shorter than this (ms) are discarded
            to ignore accidental taps (default 300ms)
        deepgram_api_key: Deepgram API key for cloud fallback.
            Leave empty to disable fallback.
    """

    model_config = {"frozen": True}

    hotkey: str = "<cmd_r>"
    model_size: str = "small.en"
    confidence_threshold: float = -0.6
    min_duration_ms: int = 300
    deepgram_api_key: str = ""


class IntentResult(BaseModel):
    """Result of intent classification for a transcribed utterance.

    Frozen — use model_copy(update=...) to create modified instances.

    Fields:
        category: one of the 5 IntentCategory literals
        transcript: the original transcribed text
        confidence: classification confidence in [0.0, 1.0]
        extracted: parsed slots dict, e.g. {"target": "auth/"} for
            mapper_command, {"task": "auth refactor"} for task_dispatch
    """

    model_config = {"frozen": True}

    category: IntentCategory
    transcript: str
    confidence: float
    extracted: dict[str, str] = {}
