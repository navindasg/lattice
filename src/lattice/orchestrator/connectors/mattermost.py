"""MattermostConnector: monitors configured channels and surfaces new messages.

Implements an async polling loop with:
- Persistent last_post_id cursor via ConnectorRegistry.get_state/set_state (DuckDB)
- Clean shutdown via asyncio.Event
- Message callback for operator notification of new messages

Design decisions:
- Polling interval controlled by ConnectorConfig.polling_interval_seconds
- stop_polling sets event but does NOT await task (caller may await if needed)
- _poll_loop uses asyncio.wait_for on stop_event.wait to avoid blocking
- Mattermost is read/write — write requires permissions.write=True
- AsyncDriver imported at module level so tests can patch via
  'lattice.orchestrator.connectors.mattermost.AsyncDriver'
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import structlog

from lattice.orchestrator.connectors.base import BaseConnector, ConnectorError
from lattice.orchestrator.connectors.models import ConnectorConfig, ConnectorResult

log = structlog.get_logger(__name__)

# Module-level import so tests can patch it.
try:
    from mattermostautodriver import AsyncDriver  # type: ignore[import]
except ImportError:
    AsyncDriver = None  # type: ignore[assignment,misc]


class MattermostConnector(BaseConnector):
    """Mattermost channel monitor with async polling loop and cursor persistence.

    Polls each configured channel_id for new messages since the last known
    post_id. The cursor is persisted to DuckDB via the ConnectorRegistry so
    it survives orchestrator restarts.

    Args:
        config: ConnectorConfig with token, base_url, and channel_ids set.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._stop_event: asyncio.Event = asyncio.Event()
        self._poll_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._registry: Any | None = None
        self._message_callback: Callable[[list[dict]], None] | None = None

    def set_registry(self, registry: Any) -> None:
        """Attach a ConnectorRegistry for last_post_id state persistence.

        Must be called before start_polling() or _poll_once().

        Args:
            registry: ConnectorRegistry instance for get_state/set_state.
        """
        self._registry = registry

    def set_message_callback(self, callback: Callable[[list[dict]], None]) -> None:
        """Register a callback invoked when new messages arrive.

        Args:
            callback: Function accepting a list of message dicts.
        """
        self._message_callback = callback

    def start_polling(self) -> None:
        """Start the async polling loop as an asyncio background task.

        Clears the stop event and creates a new poll task.
        Must be called from within a running asyncio event loop.
        """
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("mattermost_polling_started", connector=self.name)

    def stop_polling(self) -> None:
        """Signal the polling loop to stop.

        Sets the stop event. Does NOT await the task — caller can await
        self._poll_task if synchronization is needed.
        """
        self._stop_event.set()
        log.info("mattermost_polling_stopped", connector=self.name)

    async def _poll_loop(self) -> None:
        """Async polling loop — runs until stop_event is set.

        On each iteration: polls all channels, waits for polling_interval_seconds
        (or until stop_event is set), then repeats.
        """
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
            except Exception as exc:
                log.warning("mattermost_poll_error", connector=self.name, error=str(exc))

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._config.polling_interval_seconds,
                )
            except asyncio.TimeoutError:
                # Normal — timeout means "continue polling"
                pass

    async def _poll_once(self) -> list[dict]:
        """Poll all configured channels once and return new messages.

        Reads last_post_id per channel from registry, fetches new posts,
        updates last_post_id in registry, invokes message callback if set.

        Returns:
            List of message dicts (may be empty if no new messages).
        """
        if AsyncDriver is None:
            raise ConnectorError("mattermostautodriver is not installed")

        driver = AsyncDriver({
            "url": self._config.base_url,
            "token": self._config.token,
            "scheme": "http" if self._config.base_url.startswith("http://") else "https",
            "port": 443,
        })

        all_messages: list[dict] = []

        for channel_id in self._config.channel_ids:
            state_key = f"last_post_id_{channel_id}"
            last_post_id = (
                self._registry.get_state(self.name, state_key)
                if self._registry is not None
                else None
            )

            kwargs: dict[str, Any] = {"channel_id": channel_id}
            if last_post_id:
                kwargs["after"] = last_post_id

            response = await driver.posts.get_posts_for_channel(**kwargs)

            posts: dict = response.get("posts", {})
            order: list[str] = response.get("order", [])

            new_messages = []
            for post_id in order:
                post = posts.get(post_id, {})
                new_messages.append({
                    "id": post_id,
                    "message": post.get("message", ""),
                    "user_id": post.get("user_id", ""),
                    "channel_id": channel_id,
                })

            if new_messages and self._registry is not None:
                # Persist the latest post_id as cursor
                new_last_post_id = order[0] if order else last_post_id
                if new_last_post_id:
                    self._registry.set_state(self.name, state_key, new_last_post_id)

            all_messages.extend(new_messages)

        if all_messages and self._message_callback is not None:
            self._message_callback(all_messages)

        return all_messages

    async def fetch(self, query: str, **kwargs: object) -> ConnectorResult:
        """Fetch latest messages from configured Mattermost channels.

        Runs _poll_once() and formats results as connector content.

        Args:
            query: 'latest' or 'messages' to fetch recent messages.
            **kwargs: Ignored.

        Returns:
            ConnectorResult with delivery_mode='ndjson' and content
            prefixed with '[Source: Mattermost #channel_id]' for each channel.
        """
        messages = await self._poll_once()

        # Group by channel and format
        channel_sections: list[str] = []
        channels_seen: set[str] = set()
        for msg in messages:
            ch = msg.get("channel_id", "unknown")
            if ch not in channels_seen:
                channels_seen.add(ch)
                channel_sections.append(f"[Source: Mattermost #{ch}]")
            user = msg.get("user_id", "unknown")
            text = msg.get("message", "")
            channel_sections.append(f"[{user}] {text}")

        if not channel_sections:
            # Include at least the first configured channel in the prefix
            first_channel = self._config.channel_ids[0] if self._config.channel_ids else "unknown"
            channel_sections = [f"[Source: Mattermost #{first_channel}]", "No new messages."]

        content = "\n".join(channel_sections)

        return ConnectorResult(
            success=True,
            source="mattermost",
            content=content,
            delivery_mode="ndjson",
            metadata={"channels": str(len(self._config.channel_ids))},
        )

    async def write(self, content: str, **kwargs: object) -> ConnectorResult:
        """Post a message to a Mattermost channel.

        Requires permissions.write=True on the ConnectorConfig.

        Args:
            content: The message text to post.
            **kwargs:
                channel_id (str): Target channel ID.

        Returns:
            ConnectorResult(success=True) on success, (success=False) if blocked.

        Raises:
            ConnectorError: On Mattermost API failure.
        """
        if not self._config.permissions.write:
            return ConnectorResult(
                success=False,
                source="mattermost",
                error="Write permission denied — set permissions.write=True in ConnectorConfig",
            )

        channel_id = str(kwargs.get("channel_id", ""))

        if AsyncDriver is None:
            raise ConnectorError("mattermostautodriver is not installed")

        driver = AsyncDriver({
            "url": self._config.base_url,
            "token": self._config.token,
            "scheme": "http" if self._config.base_url.startswith("http://") else "https",
            "port": 443,
        })

        try:
            await driver.posts.create_post(options={
                "channel_id": channel_id,
                "message": content,
            })
        except Exception as exc:
            raise ConnectorError(f"Mattermost write failed: {exc}") from exc

        log.info("mattermost_message_posted", channel_id=channel_id)

        return ConnectorResult(
            success=True,
            source="mattermost",
            content=f"Message posted to #{channel_id}",
            delivery_mode="ndjson",
        )

    async def health_check(self) -> bool:
        """Check Mattermost connectivity via the system ping endpoint.

        Returns:
            True if /api/v4/system/ping succeeds, False on any exception.
        """
        if AsyncDriver is None:
            raise ConnectorError("mattermostautodriver is not installed")

        driver = AsyncDriver({
            "url": self._config.base_url,
            "token": self._config.token,
            "scheme": "http" if self._config.base_url.startswith("http://") else "https",
            "port": 443,
        })

        try:
            await driver.client.get("/api/v4/system/ping")
            return True
        except Exception:
            return False
