"""Voice pipeline subpackage for lattice.orchestrator.

Provides push-to-talk audio capture, speech-to-text transcription,
intent classification, and routing for operator voice commands.

All public classes are re-exported here for convenient access:
    from lattice.orchestrator.voice import VoicePipeline, IntentRouter
"""
from lattice.orchestrator.voice.capture import AudioCapture
from lattice.orchestrator.voice.hotkey import HotkeyListener
from lattice.orchestrator.voice.intent import IntentClassifier
from lattice.orchestrator.voice.models import IntentCategory, IntentResult, VoiceConfig
from lattice.orchestrator.voice.pipeline import VoicePipeline
from lattice.orchestrator.voice.router import IntentRouter, RouteResult
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
