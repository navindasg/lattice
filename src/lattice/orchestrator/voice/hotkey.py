"""Hotkey listener with thread-to-asyncio bridge.

pynput runs its keyboard listener in a daemon thread. The orchestrator's
asyncio event loop runs in the main thread. This module bridges pynput
callbacks to the asyncio world using asyncio.Queue and call_soon_threadsafe.

See RESEARCH.md Pattern 1 and Pitfalls 1, 5 for macOS hotkey limitations.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import structlog

logger = structlog.get_logger(__name__)

# Lazy-load pynput to avoid ImportError in headless environments (CI,
# containers) that lack an X server or display. The module-level name
# is set on first access so existing patch targets keep working.
keyboard: object | None = None


def _ensure_keyboard():  # noqa: ANN202
    global keyboard  # noqa: PLW0603
    if keyboard is None:
        from pynput import keyboard as _kb
        keyboard = _kb
    return keyboard


class HotkeyListener:
    """Listens for a configurable hotkey and bridges press/release events to asyncio.

    Events are enqueued as strings: "press" or "release". The async caller
    can consume events via the async generator `events()`.

    Args:
        hotkey_str: pynput key name string, e.g. "<f13>". Angle brackets are
            stripped and matched against the keyboard.Key enum. If no match,
            falls back to keyboard.KeyCode.from_char(first_char).
        loop: The running asyncio event loop. Required for thread-safe
            call_soon_threadsafe bridging.
    """

    def __init__(self, hotkey_str: str, loop: asyncio.AbstractEventLoop) -> None:
        _ensure_keyboard()
        self._hotkey_str = hotkey_str
        self._loop = loop
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._listener: keyboard.Listener | None = None
        self._target_key = self._parse_hotkey(hotkey_str)

    def _parse_hotkey(self, hotkey_str: str) -> keyboard.Key | keyboard.KeyCode:
        """Convert a hotkey string like '<f13>' to a pynput Key or KeyCode.

        Strips surrounding angle brackets, then looks up the key name in
        keyboard.Key enum. Falls back to keyboard.KeyCode.from_char() for
        single character keys or unknown names.
        """
        kb = _ensure_keyboard()
        # Strip angle brackets: "<f13>" → "f13"
        key_name = hotkey_str.strip("<>")
        try:
            return getattr(kb.Key, key_name)
        except AttributeError:
            return kb.KeyCode.from_char(key_name[0] if key_name else hotkey_str[0])

    def _matches_hotkey(self, key: keyboard.Key | keyboard.KeyCode | None) -> bool:
        """Return True if key matches the configured target key."""
        return key == self._target_key

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        """pynput press callback — runs in the pynput daemon thread."""
        if self._matches_hotkey(key):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, "press")

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        """pynput release callback — runs in the pynput daemon thread."""
        if self._matches_hotkey(key):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, "release")

    def start(self) -> None:
        """Create and start the pynput keyboard listener (daemon thread).

        Note: pynput Listener.stop() permanently terminates the listener.
        Call start() to create a fresh listener; never restart a stopped one.
        """
        kb = _ensure_keyboard()
        self._listener = kb.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            daemon=True,
        )
        self._listener.start()
        logger.info("hotkey_listener_started", hotkey=self._hotkey_str)

    def stop(self) -> None:
        """Stop the keyboard listener and release resources."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            logger.info("hotkey_listener_stopped")

    async def events(self) -> AsyncIterator[str]:
        """Async generator yielding "press" and "release" events.

        Yields:
            "press" when the configured hotkey is pressed.
            "release" when the configured hotkey is released.
        """
        while True:
            yield await self._queue.get()
