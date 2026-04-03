"""Model factory — returns a BaseChatModel instance for a given tier.

All model names and parameters come from LatticeSettings/lattice.yaml.
No model name strings are hardcoded here.
"""
from __future__ import annotations

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from lattice.llm.config import LatticeSettings

log = structlog.get_logger(__name__)


def get_model(tier: str, settings: LatticeSettings | None = None) -> BaseChatModel:
    """Return a configured BaseChatModel for the given tier.

    Args:
        tier: One of "gold", "silver", or "bronze".
        settings: Optional pre-built LatticeSettings (used in tests to avoid .env reads).

    Returns:
        A BaseChatModel subclass (ChatAnthropic, ChatOpenAI, or ChatOllama).

    Raises:
        ValueError: If the tier name or provider is unknown.
    """
    if settings is None:
        settings = LatticeSettings()

    if not hasattr(settings, tier):
        raise ValueError(f"Unknown tier: {tier!r}. Valid tiers: gold, silver, bronze")

    tier_config = getattr(settings, tier)

    log.debug("get_model", tier=tier, provider=tier_config.provider)

    if tier_config.provider == "anthropic":
        return ChatAnthropic(
            model=tier_config.model,
            temperature=tier_config.temperature,
            max_tokens=tier_config.max_tokens,
            api_key=settings.anthropic_api_key,
        )

    if tier_config.provider == "openai":
        kwargs: dict = {
            "model": tier_config.model,
            "temperature": tier_config.temperature,
            "max_tokens": tier_config.max_tokens,
            "api_key": settings.openai_api_key,
        }
        if tier_config.base_url:
            kwargs["base_url"] = tier_config.base_url
        return ChatOpenAI(**kwargs)

    if tier_config.provider == "ollama":
        return ChatOllama(
            model=tier_config.model,
            temperature=tier_config.temperature,
        )

    raise ValueError(f"Unknown provider: {tier_config.provider!r}. Valid providers: anthropic, openai, ollama")
