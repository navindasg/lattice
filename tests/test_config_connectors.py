"""Tests for LatticeSettings connectors field extension.

Verifies that LatticeSettings.connectors loads ConnectorConfig list from YAML
and defaults to empty list when not configured.
"""
from lattice.llm.config import LatticeSettings
from lattice.orchestrator.connectors.models import ConnectorConfig


class TestLatticeSettingsConnectors:
    def test_default_connectors_is_empty_list(self) -> None:
        """LatticeSettings() should have connectors defaulting to empty list."""
        settings = LatticeSettings()
        assert settings.connectors == []

    def test_connectors_empty_init(self) -> None:
        """Explicitly passing connectors=[] should work."""
        settings = LatticeSettings(connectors=[])
        assert settings.connectors == []

    def test_connectors_with_tavily_config(self) -> None:
        """LatticeSettings with tavily ConnectorConfig stored correctly."""
        cfg = ConnectorConfig(name="tavily", connector_type="tavily")
        settings = LatticeSettings(connectors=[cfg])
        assert len(settings.connectors) == 1
        assert settings.connectors[0].name == "tavily"

    def test_connectors_permissions_default(self) -> None:
        """ConnectorConfig inside LatticeSettings has default read=True permissions."""
        cfg = ConnectorConfig(name="tavily", connector_type="tavily")
        settings = LatticeSettings(connectors=[cfg])
        assert settings.connectors[0].permissions.read is True

    def test_connectors_multiple(self) -> None:
        """LatticeSettings can hold multiple connector configs."""
        cfgs = [
            ConnectorConfig(name="tavily", connector_type="tavily"),
            ConnectorConfig(name="gh", connector_type="github"),
            ConnectorConfig(name="mm", connector_type="mattermost"),
        ]
        settings = LatticeSettings(connectors=cfgs)
        assert len(settings.connectors) == 3
        names = [c.name for c in settings.connectors]
        assert "tavily" in names
        assert "gh" in names
        assert "mm" in names

    def test_connectors_field_type(self) -> None:
        """settings.connectors should be a list of ConnectorConfig instances."""
        cfg = ConnectorConfig(name="gh", connector_type="github")
        settings = LatticeSettings(connectors=[cfg])
        assert isinstance(settings.connectors, list)
        assert all(isinstance(c, ConnectorConfig) for c in settings.connectors)
