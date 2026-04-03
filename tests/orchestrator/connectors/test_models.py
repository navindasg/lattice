"""Tests for connector data models.

Covers:
- ConnectorPermissions: frozen model with read/write defaults
- ConnectorConfig: frozen model with Literal connector_type validation
- ConnectorResult: frozen model with delivery_mode Literal, success/error fields
- ConnectorState: frozen model with status Literal
"""
import pytest
from pydantic import ValidationError

from lattice.orchestrator.connectors.models import (
    ConnectorConfig,
    ConnectorPermissions,
    ConnectorResult,
    ConnectorState,
)


# ---------------------------------------------------------------------------
# ConnectorPermissions
# ---------------------------------------------------------------------------


class TestConnectorPermissions:
    def test_default_read_is_true(self) -> None:
        perms = ConnectorPermissions()
        assert perms.read is True

    def test_default_write_is_false(self) -> None:
        perms = ConnectorPermissions()
        assert perms.write is False

    def test_explicit_read_write(self) -> None:
        perms = ConnectorPermissions(read=True, write=True)
        assert perms.read is True
        assert perms.write is True

    def test_frozen_prevents_mutation(self) -> None:
        perms = ConnectorPermissions(read=True, write=False)
        with pytest.raises(Exception):
            perms.read = False  # type: ignore[misc]

    def test_frozen_config_set(self) -> None:
        assert ConnectorPermissions.model_config.get("frozen") is True


# ---------------------------------------------------------------------------
# ConnectorConfig
# ---------------------------------------------------------------------------


class TestConnectorConfigValid:
    def test_creates_with_name_and_type(self) -> None:
        cfg = ConnectorConfig(name="tavily", connector_type="tavily")
        assert cfg.name == "tavily"
        assert cfg.connector_type == "tavily"

    def test_github_type_validates(self) -> None:
        cfg = ConnectorConfig(name="gh", connector_type="github")
        assert cfg.connector_type == "github"

    def test_mattermost_type_validates(self) -> None:
        cfg = ConnectorConfig(name="mm", connector_type="mattermost")
        assert cfg.connector_type == "mattermost"

    def test_enabled_defaults_to_true(self) -> None:
        cfg = ConnectorConfig(name="tavily", connector_type="tavily")
        assert cfg.enabled is True

    def test_permissions_defaults_to_read_only(self) -> None:
        cfg = ConnectorConfig(name="tavily", connector_type="tavily")
        assert cfg.permissions.read is True
        assert cfg.permissions.write is False

    def test_api_key_defaults_to_empty(self) -> None:
        cfg = ConnectorConfig(name="tavily", connector_type="tavily")
        assert cfg.api_key == ""

    def test_token_defaults_to_empty(self) -> None:
        cfg = ConnectorConfig(name="tavily", connector_type="tavily")
        assert cfg.token == ""

    def test_repo_defaults_to_empty(self) -> None:
        cfg = ConnectorConfig(name="gh", connector_type="github")
        assert cfg.repo == ""

    def test_base_url_defaults_to_empty(self) -> None:
        cfg = ConnectorConfig(name="mm", connector_type="mattermost")
        assert cfg.base_url == ""

    def test_channel_ids_defaults_to_empty_list(self) -> None:
        cfg = ConnectorConfig(name="mm", connector_type="mattermost")
        assert cfg.channel_ids == []

    def test_polling_interval_defaults(self) -> None:
        cfg = ConnectorConfig(name="tavily", connector_type="tavily")
        assert cfg.polling_interval_seconds == 60

    def test_breaker_cooldown_defaults(self) -> None:
        cfg = ConnectorConfig(name="tavily", connector_type="tavily")
        assert cfg.breaker_cooldown_seconds == 120

    def test_breaker_has_default_error_limit(self) -> None:
        cfg = ConnectorConfig(name="tavily", connector_type="tavily")
        assert cfg.breaker.consecutive_error_limit == 3


class TestConnectorConfigInvalid:
    def test_invalid_connector_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            ConnectorConfig(name="bad", connector_type="invalid")  # type: ignore[arg-type]

    def test_slack_type_not_supported(self) -> None:
        with pytest.raises(ValidationError):
            ConnectorConfig(name="slack", connector_type="slack")  # type: ignore[arg-type]


class TestConnectorConfigFrozen:
    def test_is_frozen(self) -> None:
        cfg = ConnectorConfig(name="tavily", connector_type="tavily")
        with pytest.raises(Exception):
            cfg.name = "other"  # type: ignore[misc]

    def test_frozen_config_set(self) -> None:
        assert ConnectorConfig.model_config.get("frozen") is True


# ---------------------------------------------------------------------------
# ConnectorResult
# ---------------------------------------------------------------------------


class TestConnectorResultValid:
    def test_success_result_with_content(self) -> None:
        result = ConnectorResult(success=True, source="tavily", content="hello", delivery_mode="ndjson")
        assert result.success is True
        assert result.source == "tavily"
        assert result.content == "hello"
        assert result.delivery_mode == "ndjson"

    def test_failure_result_with_error(self) -> None:
        result = ConnectorResult(success=False, source="github", error="auth failed")
        assert result.success is False
        assert result.error == "auth failed"

    def test_soul_file_delivery_mode(self) -> None:
        result = ConnectorResult(success=True, source="tavily", delivery_mode="soul_file")
        assert result.delivery_mode == "soul_file"

    def test_content_defaults_to_empty(self) -> None:
        result = ConnectorResult(success=True, source="tavily")
        assert result.content == ""

    def test_error_defaults_to_empty(self) -> None:
        result = ConnectorResult(success=True, source="tavily")
        assert result.error == ""

    def test_delivery_mode_defaults_to_ndjson(self) -> None:
        result = ConnectorResult(success=True, source="tavily")
        assert result.delivery_mode == "ndjson"

    def test_metadata_defaults_to_empty_dict(self) -> None:
        result = ConnectorResult(success=True, source="tavily")
        assert result.metadata == {}

    def test_metadata_can_be_set(self) -> None:
        result = ConnectorResult(success=True, source="tavily", metadata={"page": "1"})
        assert result.metadata == {"page": "1"}


class TestConnectorResultInvalid:
    def test_invalid_delivery_mode_raises(self) -> None:
        with pytest.raises(ValidationError):
            ConnectorResult(success=True, source="tavily", delivery_mode="json")  # type: ignore[arg-type]


class TestConnectorResultFrozen:
    def test_is_frozen(self) -> None:
        result = ConnectorResult(success=True, source="tavily")
        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ConnectorState
# ---------------------------------------------------------------------------


class TestConnectorStateValid:
    def test_online_status(self) -> None:
        state = ConnectorState(
            name="tavily",
            connector_type="tavily",
            status="online",
            registered_at="2026-01-01T00:00:00+00:00",
        )
        assert state.status == "online"

    def test_offline_status(self) -> None:
        state = ConnectorState(
            name="tavily",
            connector_type="tavily",
            status="offline",
            registered_at="2026-01-01T00:00:00+00:00",
        )
        assert state.status == "offline"

    def test_tripped_status(self) -> None:
        state = ConnectorState(
            name="tavily",
            connector_type="tavily",
            status="tripped",
            registered_at="2026-01-01T00:00:00+00:00",
        )
        assert state.status == "tripped"

    def test_last_used_defaults_to_none(self) -> None:
        state = ConnectorState(
            name="tavily",
            connector_type="tavily",
            status="online",
            registered_at="2026-01-01T00:00:00+00:00",
        )
        assert state.last_used is None

    def test_trip_time_defaults_to_none(self) -> None:
        state = ConnectorState(
            name="tavily",
            connector_type="tavily",
            status="online",
            registered_at="2026-01-01T00:00:00+00:00",
        )
        assert state.trip_time is None


class TestConnectorStateInvalid:
    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValidationError):
            ConnectorState(
                name="tavily",
                connector_type="tavily",
                status="active",  # type: ignore[arg-type]
                registered_at="2026-01-01T00:00:00+00:00",
            )


class TestConnectorStateFrozen:
    def test_is_frozen(self) -> None:
        state = ConnectorState(
            name="tavily",
            connector_type="tavily",
            status="online",
            registered_at="2026-01-01T00:00:00+00:00",
        )
        with pytest.raises(Exception):
            state.status = "offline"  # type: ignore[misc]
