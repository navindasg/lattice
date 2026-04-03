"""Tests for get_model() factory function."""
import inspect

import pytest

from lattice.llm.config import LatticeSettings, ModelTierConfig
from lattice.llm.factory import get_model
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama


def make_settings(gold_provider="anthropic", silver_provider="openai", bronze_provider="ollama"):
    """Build a LatticeSettings with predictable tier configs for testing."""
    return LatticeSettings(
        _env_file=None,
        _yaml_file=None,
        anthropic_api_key="test-ant-key",
        openai_api_key="test-oai-key",
        gold=ModelTierConfig(provider=gold_provider, model="claude-test", temperature=0.0, max_tokens=100),
        silver=ModelTierConfig(
            provider=silver_provider,
            model="kimi-test",
            temperature=0.0,
            max_tokens=100,
            base_url="https://api.moonshot.cn/v1",
        ),
        bronze=ModelTierConfig(provider=bronze_provider, model="llama-test", temperature=0.0, max_tokens=100),
    )


class TestGetModel:
    def test_gold_returns_chat_anthropic(self):
        settings = make_settings()
        result = get_model("gold", settings=settings)
        assert isinstance(result, ChatAnthropic)

    def test_silver_returns_chat_openai(self):
        settings = make_settings()
        result = get_model("silver", settings=settings)
        assert isinstance(result, ChatOpenAI)

    def test_silver_has_base_url(self):
        settings = make_settings()
        result = get_model("silver", settings=settings)
        assert isinstance(result, ChatOpenAI)
        # Verify the base_url was set — ChatOpenAI stores it as openai_api_base or base_url
        base = getattr(result, "openai_api_base", None) or getattr(result, "base_url", None)
        assert base is not None and "moonshot" in str(base)

    def test_bronze_returns_chat_ollama(self):
        settings = make_settings()
        result = get_model("bronze", settings=settings)
        assert isinstance(result, ChatOllama)

    def test_invalid_tier_raises_value_error(self):
        settings = make_settings()
        with pytest.raises(ValueError, match="Unknown tier"):
            get_model("platinum", settings=settings)

    def test_unknown_provider_raises_value_error(self):
        settings = LatticeSettings(
            _env_file=None,
            _yaml_file=None,
            anthropic_api_key="test-key",
            openai_api_key="test-key",
            gold=ModelTierConfig(provider="unsupported_provider", model="test-model"),
            silver=ModelTierConfig(provider="openai", model="test"),
            bronze=ModelTierConfig(provider="ollama", model="test"),
        )
        with pytest.raises(ValueError, match="Unknown provider"):
            get_model("gold", settings=settings)

    def test_no_model_names_in_factory_source(self):
        """Verify factory.py has no hardcoded model name string literals.

        Model names must come from config/YAML, not be embedded in source code.
        We check for known model name strings that would appear in string literals,
        not import names like 'ChatOllama' which legitimately contain 'ollama'.
        """
        import lattice.llm.factory as factory_module
        import re

        source_file = inspect.getfile(factory_module)
        with open(source_file) as f:
            source = f.read()

        # These are model name strings that must NOT appear as string literals
        # e.g. "claude-opus-4-5", "kimi-k1.5", "llama3.2"
        forbidden_literals = [
            r'"claude-',
            r"'claude-",
            r'"kimi-',
            r"'kimi-",
            r'"llama3',
            r"'llama3",
            r'"gpt-',
            r"'gpt-",
        ]
        for pattern in forbidden_literals:
            assert not re.search(pattern, source), (
                f"Hardcoded model name pattern {pattern!r} found in factory.py — "
                "all model names must come from config"
            )

    def test_api_keys_not_hardcoded(self):
        """Verify factory.py has no hardcoded API key strings."""
        import lattice.llm.factory as factory_module

        source_file = inspect.getfile(factory_module)
        with open(source_file) as f:
            source = f.read()

        assert "sk-ant-" not in source
        assert "sk-proj-" not in source
