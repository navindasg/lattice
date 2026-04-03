"""BaseConnector ABC and ConnectorError for the MCP connector system.

BaseConnector defines the interface all concrete connectors must implement.
ConnectorError is raised for connector-level failures.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from lattice.orchestrator.connectors.models import ConnectorConfig, ConnectorResult


class ConnectorError(Exception):
    """Raised when a connector operation fails.

    Subclasses may add structured error metadata.
    """


class BaseConnector(ABC):
    """Abstract base class for all MCP connectors.

    Concrete subclasses must implement fetch() and write().
    health_check() returns True by default; override for real health probing.

    Args:
        config: ConnectorConfig with credentials and settings for this connector.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        """The connector name from its config."""
        return self._config.name

    @property
    def config(self) -> ConnectorConfig:
        """The connector's configuration."""
        return self._config

    @abstractmethod
    async def fetch(self, query: str, **kwargs: object) -> ConnectorResult:
        """Fetch data from the connector.

        Args:
            query: The search query or resource identifier.
            **kwargs: Connector-specific parameters.

        Returns:
            ConnectorResult with success=True and content, or success=False with error.

        Raises:
            ConnectorError: On connector-level failure (propagated to registry).
        """

    @abstractmethod
    async def write(self, content: str, **kwargs: object) -> ConnectorResult:
        """Write content via the connector.

        Args:
            content: The content to write.
            **kwargs: Connector-specific parameters.

        Returns:
            ConnectorResult with success=True, or success=False with error.

        Raises:
            ConnectorError: On connector-level failure (propagated to registry).
        """

    async def health_check(self) -> bool:
        """Check if the connector is reachable.

        Returns:
            True by default. Override to perform real connectivity checks.
        """
        return True
