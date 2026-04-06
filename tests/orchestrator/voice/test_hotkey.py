"""Tests for lattice.orchestrator.voice.hotkey — HotkeyListener."""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

pynput = pytest.importorskip("pynput", reason="pynput requires a display server")

from lattice.orchestrator.voice.hotkey import HotkeyListener


class TestHotkeyListenerEventQueuing:
    def test_press_event_queued_on_matching_key(self) -> None:
        """_on_press calls loop.call_soon_threadsafe with 'press' for matching key."""
        loop = asyncio.new_event_loop()
        try:
            listener = HotkeyListener("<f13>", loop)
            listener._target_key = "f13_key"

            # Mock loop to capture call_soon_threadsafe calls directly
            mock_loop = MagicMock()
            listener._loop = mock_loop

            listener._on_press("f13_key")

            mock_loop.call_soon_threadsafe.assert_called_once()
            args = mock_loop.call_soon_threadsafe.call_args[0]
            # args = (put_nowait_fn, "press")
            assert args[1] == "press"
        finally:
            loop.close()

    def test_release_event_queued_on_matching_key(self) -> None:
        """_on_release calls loop.call_soon_threadsafe with 'release' for matching key."""
        loop = asyncio.new_event_loop()
        try:
            listener = HotkeyListener("<f13>", loop)
            listener._target_key = "f13_key"

            mock_loop = MagicMock()
            listener._loop = mock_loop

            listener._on_release("f13_key")

            mock_loop.call_soon_threadsafe.assert_called_once()
            args = mock_loop.call_soon_threadsafe.call_args[0]
            assert args[1] == "release"
        finally:
            loop.close()

    def test_non_matching_key_is_ignored(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            listener = HotkeyListener("<f13>", loop)
            listener._target_key = "f13_key"

            mock_loop = MagicMock()
            listener._loop = mock_loop

            listener._on_press("some_other_key")

            mock_loop.call_soon_threadsafe.assert_not_called()
        finally:
            loop.close()

    def test_non_matching_key_release_is_ignored(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            listener = HotkeyListener("<f13>", loop)
            listener._target_key = "f13_key"

            mock_loop = MagicMock()
            listener._loop = mock_loop

            listener._on_release("some_other_key")

            mock_loop.call_soon_threadsafe.assert_not_called()
        finally:
            loop.close()


class TestHotkeyListenerStart:
    def test_start_creates_listener_with_daemon_true(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            listener = HotkeyListener("<f13>", loop)

            with patch("lattice.orchestrator.voice.hotkey.keyboard") as mock_keyboard:
                mock_kb_listener = MagicMock()
                mock_keyboard.Listener.return_value = mock_kb_listener
                mock_keyboard.Key.f13 = object()

                listener.start()

                call_kwargs = mock_keyboard.Listener.call_args.kwargs
                assert call_kwargs.get("daemon") is True
                mock_kb_listener.start.assert_called_once()
        finally:
            loop.close()

    def test_stop_sets_listener_to_none(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            listener = HotkeyListener("<f13>", loop)
            mock_kb_listener = MagicMock()
            listener._listener = mock_kb_listener

            listener.stop()

            mock_kb_listener.stop.assert_called_once()
            assert listener._listener is None
        finally:
            loop.close()


class TestHotkeyListenerParseHotkey:
    def test_parse_f13_returns_keyboard_key(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            with patch("lattice.orchestrator.voice.hotkey.keyboard") as mock_keyboard:
                mock_f13 = object()
                mock_keyboard.Key.f13 = mock_f13
                mock_keyboard.Listener = MagicMock()

                listener = HotkeyListener("<f13>", loop)
                assert listener._target_key == mock_f13
        finally:
            loop.close()

    def test_parse_unknown_key_falls_back_to_keycode(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            with patch("lattice.orchestrator.voice.hotkey.keyboard") as mock_keyboard:
                mock_keyboard.Key = MagicMock(spec=[])  # no attribute 'xyz'
                mock_keycode = MagicMock()
                mock_keyboard.KeyCode.from_char.return_value = mock_keycode
                mock_keyboard.Listener = MagicMock()

                listener = HotkeyListener("xyz", loop)
                mock_keyboard.KeyCode.from_char.assert_called_once_with("x")
        finally:
            loop.close()
