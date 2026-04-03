"""Connector subpackage: models, base ABC, runtime registry, and concrete connectors.

Re-exports all public connector types for convenient import:
    from lattice.orchestrator.connectors import ConnectorRegistry, TavilyConnector, ...
"""
from lattice.orchestrator.connectors.base import BaseConnector, ConnectorError
from lattice.orchestrator.connectors.models import (
    ConnectorConfig,
    ConnectorPermissions,
    ConnectorResult,
    ConnectorState,
)
from lattice.orchestrator.connectors.registry import ConnectorRegistry
from lattice.orchestrator.connectors.tavily import TavilyConnector
from lattice.orchestrator.connectors.github import GitHubConnector
from lattice.orchestrator.connectors.mattermost import MattermostConnector

__all__ = [
    "BaseConnector",
    "ConnectorError",
    "ConnectorConfig",
    "ConnectorPermissions",
    "ConnectorResult",
    "ConnectorState",
    "ConnectorRegistry",
    "TavilyConnector",
    "GitHubConnector",
    "MattermostConnector",
]
