"""Tests for LatticeSettings and ModelTierConfig."""
import os
import textwrap

import pytest
from pydantic import ValidationError

from lattice.llm.config import LatticeSettings, ModelTierConfig
from pydantic_settings import YamlConfigSettingsSource


class TestModelTierConfig:
    def test_valid_tier_config(self):
        config = ModelTierConfig(provider="anthropic", model="claude-opus-4-5")
        assert config.provider == "anthropic"
        assert config.model == "claude-opus-4-5"
        assert config.temperature == 0.0
        assert config.max_tokens == 4096
        assert config.base_url is None

    def test_custom_temperature_and_tokens(self):
        config = ModelTierConfig(
            provider="openai",
            model="gpt-4",
            temperature=0.7,
            max_tokens=2048,
            base_url="https://api.example.com/v1",
        )
        assert config.temperature == 0.7
        assert config.max_tokens == 2048
        assert config.base_url == "https://api.example.com/v1"

    def test_provider_required(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ModelTierConfig(model="gpt-4")  # missing provider

    def test_model_required(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ModelTierConfig(provider="anthropic")  # missing model


class TestLatticeSettings:
    def test_log_level_default(self):
        settings = LatticeSettings()
        assert settings.log_level == "INFO"

    def test_log_level_from_env(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        settings = LatticeSettings()
        assert settings.log_level == "DEBUG"

    def test_defaults_without_yaml(self):
        settings = LatticeSettings()
        assert settings.gold.provider == "anthropic"
        assert settings.silver.provider == "openai"
        assert settings.bronze.provider == "ollama"

    def test_yaml_source_in_customise_sources(self):
        """CRITICAL: YamlConfigSettingsSource must be in the source chain."""
        from pydantic_settings import PydanticBaseSettingsSource

        # Call settings_customise_sources with dummy args and verify YAML source is included
        class DummySource(PydanticBaseSettingsSource):
            def get_field_value(self, field, field_name):
                return None, field_name, False

            def __call__(self):
                return {}

        dummy = DummySource(LatticeSettings)
        sources = LatticeSettings.settings_customise_sources(
            settings_cls=LatticeSettings,
            init_settings=dummy,
            env_settings=dummy,
            dotenv_settings=dummy,
            file_secret_settings=dummy,
        )
        source_types = [type(s) for s in sources]
        assert any(
            issubclass(t, YamlConfigSettingsSource) for t in source_types
        ), "YamlConfigSettingsSource must be included in settings_customise_sources"

    def test_loads_from_yaml(self, tmp_path, monkeypatch):
        """Settings loaded from a temp lattice.yaml should override defaults."""
        yaml_content = textwrap.dedent("""
            gold:
              provider: anthropic
              model: claude-test-model
              temperature: 0.5
              max_tokens: 1000
            silver:
              provider: openai
              model: test-silver-model
              temperature: 0.1
              max_tokens: 2000
              base_url: https://test.api.com/v1
            bronze:
              provider: ollama
              model: test-bronze-model
              temperature: 0.0
              max_tokens: 500
        """)
        yaml_file = tmp_path / "lattice.yaml"
        yaml_file.write_text(yaml_content)
        monkeypatch.chdir(tmp_path)

        settings = LatticeSettings()
        assert settings.gold.model == "claude-test-model"
        assert settings.gold.temperature == 0.5
        assert settings.silver.model == "test-silver-model"
        assert settings.silver.base_url == "https://test.api.com/v1"
        assert settings.bronze.model == "test-bronze-model"

    def test_api_keys_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-ant-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-oai-key")
        settings = LatticeSettings()
        assert settings.anthropic_api_key == "test-ant-key"
        assert settings.openai_api_key == "test-oai-key"


class TestProjectConfig:
    """Tests for ProjectConfig and per-project configuration models."""

    def test_project_config_all_fields(self):
        """ProjectConfig validates all specified fields."""
        from lattice.llm.config import (
            ProjectConfig,
            ModelProfileConfig,
            ConnectorProjectConfig,
            MapperProjectConfig,
            OrchestratorProjectConfig,
        )
        config = ProjectConfig(
            name="my-project",
            root="/absolute/path/to/project",
            model_profile=ModelProfileConfig(tier="gold"),
            connectors=ConnectorProjectConfig(
                allowed=["github", "tavily"],
                github_repo="org/repo",
            ),
            mapper=MapperProjectConfig(auto_remap=False, default_tier="gold"),
            orchestrator=OrchestratorProjectConfig(
                max_instances=5,
                breaker_overrides={"iteration_cap": 100},
            ),
        )
        assert config.name == "my-project"
        assert config.root == "/absolute/path/to/project"
        assert config.model_profile.tier == "gold"
        assert config.connectors.allowed == ["github", "tavily"]
        assert config.connectors.github_repo == "org/repo"
        assert config.mapper.auto_remap is False
        assert config.mapper.default_tier == "gold"
        assert config.orchestrator.max_instances == 5
        assert config.orchestrator.breaker_overrides == {"iteration_cap": 100}

    def test_project_config_missing_name_raises(self):
        """ProjectConfig rejects missing name with ValidationError."""
        from lattice.llm.config import ProjectConfig
        with pytest.raises(ValidationError):
            ProjectConfig(root="/some/path")  # missing name

    def test_project_config_frozen(self):
        """ProjectConfig is frozen — assignment raises error."""
        from lattice.llm.config import ProjectConfig
        config = ProjectConfig(name="proj", root="/path")
        with pytest.raises((ValidationError, TypeError)):
            config.name = "changed"  # type: ignore[misc]

    def test_lattice_settings_projects_default_empty(self):
        """LatticeSettings.projects field is list[ProjectConfig], defaults to empty list."""
        settings = LatticeSettings()
        assert hasattr(settings, "projects")
        assert settings.projects == []

    def test_project_config_defaults(self):
        """ProjectConfig sub-models have correct defaults."""
        from lattice.llm.config import ProjectConfig
        config = ProjectConfig(name="proj", root="/path")
        assert config.model_profile.tier == "silver"
        assert config.connectors.allowed == []
        assert config.connectors.github_repo is None
        assert config.mapper.auto_remap is True
        assert config.mapper.default_tier == "silver"
        assert config.orchestrator.max_instances == 3
        assert config.orchestrator.breaker_overrides == {}
