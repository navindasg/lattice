"""Tests for MattermostConnector.

Tests use mocked AsyncDriver — no real Mattermost API calls.
Covers: fetch, poll_once, polling lifecycle, last_post_id persistence,
message callback, write permission guard, health check.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from lattice.orchestrator.connectors.base import ConnectorError
from lattice.orchestrator.connectors.mattermost import MattermostConnector
from lattice.orchestrator.connectors.models import ConnectorConfig, ConnectorPermissions


@pytest.fixture()
def config() -> ConnectorConfig:
    return ConnectorConfig(
        name="mattermost",
        connector_type="mattermost",
        token="test-token",
        base_url="http://mattermost.example.com",
        channel_ids=["ch1", "ch2"],
        polling_interval_seconds=1,  # Fast for tests
    )


@pytest.fixture()
def write_config() -> ConnectorConfig:
    return ConnectorConfig(
        name="mattermost-write",
        connector_type="mattermost",
        token="test-token",
        base_url="http://mattermost.example.com",
        channel_ids=["ch1"],
        polling_interval_seconds=1,
        permissions=ConnectorPermissions(read=True, write=True),
    )


@pytest.fixture()
def connector(config: ConnectorConfig) -> MattermostConnector:
    return MattermostConnector(config)


@pytest.fixture()
def write_connector(write_config: ConnectorConfig) -> MattermostConnector:
    return MattermostConnector(write_config)


def _make_registry(state: dict[str, str] | None = None) -> MagicMock:
    """Create a mock ConnectorRegistry with configurable state."""
    registry = MagicMock()
    stored = state or {}
    registry.get_state.side_effect = lambda name, key: stored.get(key)
    registry.set_state.side_effect = lambda name, key, value: stored.update({key: value})
    return registry


def _make_posts_response(messages: list[dict]) -> dict:
    """Build a mock Mattermost posts API response."""
    posts = {}
    order = []
    for i, msg in enumerate(messages):
        post_id = msg.get("id", f"post{i}")
        posts[post_id] = {
            "id": post_id,
            "message": msg.get("message", ""),
            "user_id": msg.get("user_id", "user1"),
        }
        order.append(post_id)
    return {"posts": posts, "order": order}


# ---------------------------------------------------------------------------
# _poll_once tests
# ---------------------------------------------------------------------------


class TestPollOnce:
    def test_poll_once_returns_messages_for_configured_channels(
        self, connector: MattermostConnector
    ) -> None:
        """_poll_once returns new messages since last_post_id for each channel."""
        registry = _make_registry()
        connector.set_registry(registry)

        posts_response = _make_posts_response([
            {"id": "post1", "message": "Hello team", "user_id": "alice"},
        ])

        with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
            mock_driver = AsyncMock()
            mock_driver.posts.get_posts_for_channel = AsyncMock(
                return_value=posts_response
            )
            MockDriver.return_value = mock_driver

            messages = asyncio.run(connector._poll_once())

        assert len(messages) > 0

    def test_poll_once_persists_last_post_id_via_registry(
        self, connector: MattermostConnector
    ) -> None:
        """_poll_once saves last_post_id to registry.set_state after each poll."""
        state_store: dict[str, str] = {}
        registry = _make_registry(state_store)
        connector.set_registry(registry)

        posts_response = _make_posts_response([
            {"id": "newpost1", "message": "Team update"},
        ])

        with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
            mock_driver = AsyncMock()
            mock_driver.posts.get_posts_for_channel = AsyncMock(
                return_value=posts_response
            )
            MockDriver.return_value = mock_driver

            asyncio.run(connector._poll_once())

        # Registry.set_state should have been called to persist last_post_id
        registry.set_state.assert_called()

    def test_poll_once_reads_last_post_id_from_registry(
        self, connector: MattermostConnector
    ) -> None:
        """_poll_once reads last_post_id from registry.get_state on startup."""
        state_store = {"last_post_id_ch1": "existing_post_id"}
        registry = _make_registry(state_store)
        connector.set_registry(registry)

        posts_response = _make_posts_response([])

        with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
            mock_driver = AsyncMock()
            mock_driver.posts.get_posts_for_channel = AsyncMock(
                return_value=posts_response
            )
            MockDriver.return_value = mock_driver

            asyncio.run(connector._poll_once())

        # Should have called get_state to read the cursor
        registry.get_state.assert_called()

    def test_poll_once_invokes_message_callback_when_new_messages(
        self, connector: MattermostConnector
    ) -> None:
        """_poll_once calls message_callback when new messages are found."""
        registry = _make_registry()
        connector.set_registry(registry)

        callback_results = []
        connector.set_message_callback(lambda msgs: callback_results.extend(msgs))

        posts_response = _make_posts_response([
            {"id": "post1", "message": "New message!"},
        ])

        with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
            mock_driver = AsyncMock()
            mock_driver.posts.get_posts_for_channel = AsyncMock(
                return_value=posts_response
            )
            MockDriver.return_value = mock_driver

            asyncio.run(connector._poll_once())

        assert len(callback_results) > 0


# ---------------------------------------------------------------------------
# start/stop polling lifecycle tests
# ---------------------------------------------------------------------------


class TestPollingLifecycle:
    def test_start_polling_creates_task(self, connector: MattermostConnector) -> None:
        """start_polling creates an asyncio task."""
        registry = _make_registry()
        connector.set_registry(registry)

        async def run_test():
            with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
                mock_driver = AsyncMock()
                mock_driver.posts.get_posts_for_channel = AsyncMock(
                    return_value=_make_posts_response([])
                )
                MockDriver.return_value = mock_driver

                connector.start_polling()
                assert connector._poll_task is not None
                assert not connector._poll_task.done()

                # Stop and clean up
                connector.stop_polling()
                await asyncio.sleep(0.1)

        asyncio.run(run_test())

    def test_stop_polling_sets_event_and_task_completes(
        self, connector: MattermostConnector
    ) -> None:
        """stop_polling sets the stop event and poll task completes within 1 second."""
        registry = _make_registry()
        connector.set_registry(registry)

        async def run_test():
            with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
                mock_driver = AsyncMock()
                mock_driver.posts.get_posts_for_channel = AsyncMock(
                    return_value=_make_posts_response([])
                )
                MockDriver.return_value = mock_driver

                connector.start_polling()
                task = connector._poll_task
                assert task is not None

                connector.stop_polling()
                assert connector._stop_event.is_set()

                # Task should complete within 1 second
                await asyncio.wait_for(task, timeout=1.0)

        asyncio.run(run_test())


# ---------------------------------------------------------------------------
# fetch tests
# ---------------------------------------------------------------------------


class TestFetch:
    def test_fetch_latest_returns_success_result(
        self, connector: MattermostConnector
    ) -> None:
        """fetch('latest') returns ConnectorResult(success=True)."""
        registry = _make_registry()
        connector.set_registry(registry)

        posts_response = _make_posts_response([
            {"id": "p1", "message": "Hello", "user_id": "alice"},
        ])

        with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
            mock_driver = AsyncMock()
            mock_driver.posts.get_posts_for_channel = AsyncMock(
                return_value=posts_response
            )
            MockDriver.return_value = mock_driver

            result = asyncio.run(connector.fetch("latest"))

        assert result.success is True
        assert result.source == "mattermost"

    def test_fetch_content_prefixed_with_source_mattermost(
        self, connector: MattermostConnector
    ) -> None:
        """fetch content is prefixed with '[Source: Mattermost #channel_id]'."""
        registry = _make_registry()
        connector.set_registry(registry)

        posts_response = _make_posts_response([
            {"id": "p1", "message": "Hello"},
        ])

        with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
            mock_driver = AsyncMock()
            mock_driver.posts.get_posts_for_channel = AsyncMock(
                return_value=posts_response
            )
            MockDriver.return_value = mock_driver

            result = asyncio.run(connector.fetch("latest"))

        assert "[Source: Mattermost" in result.content

    def test_fetch_delivery_mode_is_ndjson(self, connector: MattermostConnector) -> None:
        """fetch returns delivery_mode='ndjson' (messages are short-form)."""
        registry = _make_registry()
        connector.set_registry(registry)

        posts_response = _make_posts_response([])

        with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
            mock_driver = AsyncMock()
            mock_driver.posts.get_posts_for_channel = AsyncMock(
                return_value=posts_response
            )
            MockDriver.return_value = mock_driver

            result = asyncio.run(connector.fetch("messages"))

        assert result.delivery_mode == "ndjson"


# ---------------------------------------------------------------------------
# write tests
# ---------------------------------------------------------------------------


class TestWrite:
    def test_write_without_permission_returns_error(
        self, connector: MattermostConnector
    ) -> None:
        """write returns error ConnectorResult when permissions.write is False."""
        result = asyncio.run(connector.write("hello", channel_id="ch1"))
        assert result.success is False
        assert "write" in result.error.lower() or "permission" in result.error.lower()

    def test_write_with_permission_posts_message(
        self, write_connector: MattermostConnector
    ) -> None:
        """write with permissions.write=True posts message and returns success."""
        with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
            mock_driver = AsyncMock()
            mock_driver.posts.create_post = AsyncMock(return_value={"id": "new_post"})
            MockDriver.return_value = mock_driver

            result = asyncio.run(write_connector.write("Hello team", channel_id="ch1"))

        assert result.success is True
        assert result.source == "mattermost"


# ---------------------------------------------------------------------------
# health_check tests
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_health_check_calls_system_ping(self, connector: MattermostConnector) -> None:
        """health_check calls /api/v4/system/ping via AsyncDriver."""
        with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
            mock_driver = AsyncMock()
            mock_driver.client = AsyncMock()
            mock_driver.client.get = AsyncMock(return_value={"status": "OK"})
            MockDriver.return_value = mock_driver

            result = asyncio.run(connector.health_check())

        assert result is True

    def test_health_check_returns_false_on_exception(
        self, connector: MattermostConnector
    ) -> None:
        """health_check returns False when AsyncDriver raises an exception."""
        with patch("lattice.orchestrator.connectors.mattermost.AsyncDriver") as MockDriver:
            mock_driver = AsyncMock()
            mock_driver.client = AsyncMock()
            mock_driver.client.get = AsyncMock(
                side_effect=Exception("Connection refused")
            )
            MockDriver.return_value = mock_driver

            result = asyncio.run(connector.health_check())

        assert result is False
