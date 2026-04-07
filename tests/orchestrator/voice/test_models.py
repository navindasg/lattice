"""Tests for lattice.orchestrator.voice.models — VoiceConfig and IntentResult."""
from collections import Counter

import pytest
from pydantic import ValidationError

from lattice.orchestrator.voice.models import IntentCategory, IntentResult, VoiceConfig
from lattice.llm.config import LatticeSettings
from tests.orchestrator.voice.fixtures.transcripts import TRANSCRIPT_FIXTURES


class TestVoiceConfigDefaults:
    def test_default_hotkey(self) -> None:
        cfg = VoiceConfig()
        assert cfg.hotkey == "<cmd_r>"

    def test_default_model_size(self) -> None:
        cfg = VoiceConfig()
        assert cfg.model_size == "small.en"

    def test_default_confidence_threshold(self) -> None:
        cfg = VoiceConfig()
        assert cfg.confidence_threshold == -0.6

    def test_default_min_duration_ms(self) -> None:
        cfg = VoiceConfig()
        assert cfg.min_duration_ms == 300

    def test_default_deepgram_api_key(self) -> None:
        cfg = VoiceConfig()
        assert cfg.deepgram_api_key == ""

    def test_hotkey_override(self) -> None:
        cfg = VoiceConfig(hotkey="<f14>")
        assert cfg.hotkey == "<f14>"

    def test_frozen_mutation_raises(self) -> None:
        cfg = VoiceConfig()
        with pytest.raises((TypeError, ValidationError)):
            cfg.hotkey = "<f14>"  # type: ignore[misc]


class TestIntentCategory:
    def test_valid_category_task_dispatch(self) -> None:
        result = IntentResult(
            category="task_dispatch",
            transcript="start working",
            confidence=0.9,
            extracted={},
        )
        assert result.category == "task_dispatch"

    def test_valid_category_context_injection(self) -> None:
        result = IntentResult(
            category="context_injection",
            transcript="add context",
            confidence=0.9,
            extracted={},
        )
        assert result.category == "context_injection"

    def test_valid_category_status_query(self) -> None:
        result = IntentResult(
            category="status_query",
            transcript="what is the status",
            confidence=0.9,
            extracted={},
        )
        assert result.category == "status_query"

    def test_valid_category_mapper_command(self) -> None:
        result = IntentResult(
            category="mapper_command",
            transcript="map the auth directory",
            confidence=0.9,
            extracted={},
        )
        assert result.category == "mapper_command"

    def test_valid_category_unrecognized(self) -> None:
        result = IntentResult(
            category="unrecognized",
            transcript="gibberish",
            confidence=0.0,
            extracted={},
        )
        assert result.category == "unrecognized"

    def test_invalid_category_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            IntentResult(
                category="invalid_category",  # type: ignore[arg-type]
                transcript="test",
                confidence=0.9,
                extracted={},
            )


class TestIntentResultModel:
    def test_construction_with_status_query(self) -> None:
        result = IntentResult(
            category="status_query",
            transcript="what is the status",
            confidence=0.9,
            extracted={},
        )
        assert result.transcript == "what is the status"
        assert result.confidence == 0.9
        assert result.extracted == {}

    def test_frozen_mutation_raises(self) -> None:
        result = IntentResult(
            category="status_query",
            transcript="what is the status",
            confidence=0.9,
            extracted={},
        )
        with pytest.raises((TypeError, ValidationError)):
            result.category = "mapper_command"  # type: ignore[misc]

    def test_extracted_dict_defaults_to_empty(self) -> None:
        result = IntentResult(
            category="status_query",
            transcript="what is the status",
            confidence=0.9,
        )
        assert result.extracted == {}


class TestLatticeSettingsVoice:
    def test_voice_field_returns_voice_config(self) -> None:
        settings = LatticeSettings()
        assert isinstance(settings.voice, VoiceConfig)

    def test_voice_field_has_defaults(self) -> None:
        settings = LatticeSettings()
        assert settings.voice.hotkey == "<cmd_r>"
        assert settings.voice.model_size == "small.en"
        assert settings.voice.confidence_threshold == -0.6
        assert settings.voice.min_duration_ms == 300

    def test_voice_field_can_be_overridden(self) -> None:
        settings = LatticeSettings(voice=VoiceConfig(hotkey="<f14>"))
        assert settings.voice.hotkey == "<f14>"


class TestTranscriptFixtures:
    def test_fixture_set_has_entries(self) -> None:
        assert len(TRANSCRIPT_FIXTURES) >= 40

    def test_all_entries_are_tuples_of_two_strings(self) -> None:
        for utterance, category in TRANSCRIPT_FIXTURES:
            assert isinstance(utterance, str)
            assert isinstance(category, str)

    def test_at_least_10_per_category(self) -> None:
        counts = Counter(category for _, category in TRANSCRIPT_FIXTURES)
        for category in ("task_dispatch", "context_injection", "status_query", "mapper_command"):
            assert counts[category] >= 10, (
                f"Expected >= 10 fixtures for '{category}', got {counts[category]}"
            )
