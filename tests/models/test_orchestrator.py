"""Tests for ManagedInstance and MapperCommand Pydantic v2 models.

Covers:
- UUID id auto-generation for ManagedInstance
- Status validation against Literal set for ManagedInstance
- Command validation against Literal set for MapperCommand
- Frozen model enforcement
- Default values
"""
import pytest
import uuid
from pydantic import ValidationError

from lattice.models.orchestrator import ManagedInstance, MapperCommand


# ---------------------------------------------------------------------------
# ManagedInstance
# ---------------------------------------------------------------------------

class TestManagedInstanceValid:
    def test_creates_with_no_args(self):
        instance = ManagedInstance()
        assert instance.id is not None

    def test_generates_uuid_id_by_default(self):
        instance = ManagedInstance()
        parsed = uuid.UUID(instance.id)
        assert str(parsed) == instance.id

    def test_default_status_is_idle(self):
        instance = ManagedInstance()
        assert instance.status == "idle"

    def test_default_pid_is_none(self):
        instance = ManagedInstance()
        assert instance.pid is None

    def test_default_task_id_is_none(self):
        instance = ManagedInstance()
        assert instance.task_id is None

    def test_all_valid_statuses(self):
        for status in ("idle", "running", "stopped", "crashed"):
            instance = ManagedInstance(status=status)
            assert instance.status == status

    def test_two_instances_have_different_ids(self):
        i1 = ManagedInstance()
        i2 = ManagedInstance()
        assert i1.id != i2.id

    def test_accepts_pid_and_task_id(self):
        instance = ManagedInstance(pid=12345, task_id="task-abc")
        assert instance.pid == 12345
        assert instance.task_id == "task-abc"


class TestManagedInstanceInvalid:
    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            ManagedInstance(status="active")

    def test_invalid_status_running_typo_raises(self):
        with pytest.raises(ValidationError):
            ManagedInstance(status="RUNNING")


class TestManagedInstanceFrozen:
    def test_assignment_raises_error(self):
        instance = ManagedInstance()
        with pytest.raises(Exception):
            instance.status = "running"

    def test_is_frozen_config(self):
        assert ManagedInstance.model_config.get("frozen") is True


# ---------------------------------------------------------------------------
# MapperCommand
# ---------------------------------------------------------------------------

class TestMapperCommandValid:
    def test_all_valid_commands(self):
        valid_commands = [
            "map:init",
            "map:hint",
            "map:status",
            "map:stop",
            "map:doc",
            "map:gaps",
        ]
        for cmd in valid_commands:
            mc = MapperCommand(command=cmd)
            assert mc.command == cmd

    def test_default_args_is_empty_dict(self):
        mc = MapperCommand(command="map:init")
        assert mc.args == {}

    def test_default_session_id_is_none(self):
        mc = MapperCommand(command="map:init")
        assert mc.session_id is None

    def test_accepts_args_and_session_id(self):
        mc = MapperCommand(
            command="map:hint",
            args={"file": "src/main.py"},
            session_id="session-123",
        )
        assert mc.args == {"file": "src/main.py"}
        assert mc.session_id == "session-123"


class TestMapperCommandInvalid:
    def test_missing_command_raises(self):
        with pytest.raises(ValidationError):
            MapperCommand()

    def test_unknown_command_raises(self):
        with pytest.raises(ValidationError):
            MapperCommand(command="map:unknown")

    def test_arbitrary_string_raises(self):
        with pytest.raises(ValidationError):
            MapperCommand(command="not-a-command")


class TestMapperCommandFrozen:
    def test_assignment_raises_error(self):
        mc = MapperCommand(command="map:init")
        with pytest.raises(Exception):
            mc.command = "map:stop"

    def test_is_frozen_config(self):
        assert MapperCommand.model_config.get("frozen") is True
