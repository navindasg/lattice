"""Voice pipeline subpackage for lattice.orchestrator.

Provides push-to-talk audio capture, speech-to-text transcription,
intent classification, and routing for operator voice commands.

Supports CC instance control intents (cc_command, cc_approve, cc_deny,
cc_deny_redirect, cc_status, cc_interrupt) and orchestrator_freeform
for LLM-powered interpretation of unrecognized utterances.

All public classes are re-exported here for convenient access:
    from lattice.orchestrator.voice import VoicePipeline, IntentRouter

Hardware-dependent modules (capture, hotkey, stt) are lazy-loaded to avoid
ImportError in headless environments (CI, containers) where PortAudio or
X11 display are unavailable.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from lattice.orchestrator.voice.intent import IntentClassifier
from lattice.orchestrator.voice.models import IntentCategory, IntentResult, VoiceConfig
from lattice.orchestrator.voice.pipeline import VoicePipeline
from lattice.orchestrator.voice.router import IntentRouter, RouteResult

if TYPE_CHECKING:
    from lattice.orchestrator.voice.capture import AudioCapture
    from lattice.orchestrator.voice.hotkey import HotkeyListener
    from lattice.orchestrator.voice.stt import DeepgramFallback, STTProvider

__all__ = [
    "AudioCapture",
    "DeepgramFallback",
    "HotkeyListener",
    "IntentCategory",
    "IntentClassifier",
    "IntentResult",
    "IntentRouter",
    "RouteResult",
    "STTProvider",
    "VoiceConfig",
    "VoicePipeline",
]


def __getattr__(name: str) -> object:
    """Lazy-load hardware-dependent modules on first access."""
    if name == "AudioCapture":
        from lattice.orchestrator.voice.capture import AudioCapture
        return AudioCapture
    if name == "HotkeyListener":
        from lattice.orchestrator.voice.hotkey import HotkeyListener
        return HotkeyListener
    if name == "DeepgramFallback":
        from lattice.orchestrator.voice.stt import DeepgramFallback
        return DeepgramFallback
    if name == "STTProvider":
        from lattice.orchestrator.voice.stt import STTProvider
        return STTProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
