"""Pydantic-settings configuration for Lattice model tiers and application settings.

Loads API secrets from .env and model tier definitions from lattice.yaml.
CRITICAL: settings_customise_sources explicitly includes YamlConfigSettingsSource.
Without this override, the yaml_file in SettingsConfigDict is silently ignored.
"""
from __future__ import annotations

from typing import Any, Tuple, Type

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from lattice.orchestrator.connectors.models import ConnectorConfig
from lattice.orchestrator.models import ContextManagerConfig, OrchestratorConfig
from lattice.orchestrator.voice.models import VoiceConfig


class ModelTierConfig(BaseModel):
    """Configuration for a single LLM tier (Gold/Silver/Bronze)."""

    provider: str  # "anthropic" | "openai" | "ollama"
    model: str
    temperature: float = 0.0
    max_tokens: int = 8192
    base_url: str | None = None


class MapperProjectConfig(BaseModel):
    """Per-project mapper configuration."""

    auto_remap: bool = True
    default_tier: str = "silver"

    model_config = {"frozen": True}


class ConnectorProjectConfig(BaseModel):
    """Per-project connector permissions and scope."""

    allowed: list[str] = Field(default_factory=list)
    github_repo: str | None = None

    model_config = {"frozen": True}


class ModelProfileConfig(BaseModel):
    """Per-project model tier selection."""

    tier: str = "silver"

    model_config = {"frozen": True}


class OrchestratorProjectConfig(BaseModel):
    """Per-project orchestrator overrides."""

    max_instances: int = 3
    breaker_overrides: dict = Field(default_factory=dict)

    model_config = {"frozen": True}


class ProjectConfig(BaseModel):
    """Per-project configuration loaded from .lattice/config.yaml."""

    name: str
    root: str  # absolute path, resolved at load time
    model_profile: ModelProfileConfig = Field(default_factory=ModelProfileConfig)
    connectors: ConnectorProjectConfig = Field(default_factory=ConnectorProjectConfig)
    mapper: MapperProjectConfig = Field(default_factory=MapperProjectConfig)
    orchestrator: OrchestratorProjectConfig = Field(default_factory=OrchestratorProjectConfig)

    model_config = {"frozen": True}


class LatticeSettings(BaseSettings):
    """Application settings loaded from env vars, .env file, and lattice.yaml.

    Priority order (highest to lowest):
    1. Environment variables
    2. .env file values
    3. lattice.yaml values
    4. File secrets
    5. Python defaults (below)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        yaml_file="lattice.yaml",
        env_nested_delimiter="__",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    gold: ModelTierConfig = ModelTierConfig(
        provider="anthropic",
        model="claude-opus-4-5",
    )
    silver: ModelTierConfig = ModelTierConfig(
        provider="openai",
        model="kimi-k1.5",
    )
    bronze: ModelTierConfig = ModelTierConfig(
        provider="ollama",
        model="llama3.2",
    )
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    context_manager: ContextManagerConfig = Field(default_factory=ContextManagerConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    connectors: list[ConnectorConfig] = Field(default_factory=list)
    projects: list[ProjectConfig] = Field(default_factory=list)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """Wire YamlConfigSettingsSource into the source chain.

        Without this override, yaml_file in model_config is parsed but NOT activated.
        See: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
