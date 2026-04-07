"""Tests for terminal backend data models.

Covers:
- PaneInfo: creation with all fields, frozen immutability
- CCInstance: creation with user_number, frozen immutability
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from lattice.orchestrator.terminal.models import CCInstance, PaneInfo


class TestPaneInfo:
    """PaneInfo creation and immutability."""

    def test_creation_with_all_fields(self):
        """PaneInfo can be created with every required field."""
        info = PaneInfo(
            pane_id="%0",
            session_name="main",
            window_name="editor",
            pane_index=0,
            running_command="zsh",
            cwd="/home/user",
        )
        assert info.pane_id == "%0"
        assert info.session_name == "main"
        assert info.window_name == "editor"
        assert info.pane_index == 0
        assert info.running_command == "zsh"
        assert info.cwd == "/home/user"

    def test_frozen_cannot_mutate(self):
        """Frozen model raises on attribute assignment."""
        info = PaneInfo(
            pane_id="%0",
            session_name="main",
            window_name="editor",
            pane_index=0,
            running_command="zsh",
            cwd="/home/user",
        )
        with pytest.raises(ValidationError):
            info.pane_id = "%1"


class TestCCInstance:
    """CCInstance creation and immutability."""

    def test_creation_with_user_number(self):
        """CCInstance includes a stable user-facing number."""
        inst = CCInstance(
            pane_id="%2",
            session_name="work",
            window_name="claude",
            user_number=1,
            running_command="claude",
            cwd="/projects/lattice",
        )
        assert inst.user_number == 1
        assert inst.pane_id == "%2"
        assert inst.running_command == "claude"

    def test_frozen_cannot_mutate(self):
        """Frozen model raises on attribute assignment."""
        inst = CCInstance(
            pane_id="%2",
            session_name="work",
            window_name="claude",
            user_number=1,
            running_command="claude",
            cwd="/projects/lattice",
        )
        with pytest.raises(ValidationError):
            inst.user_number = 2
