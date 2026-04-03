"""Tests for MappingSession Pydantic v2 model.

Covers:
- UUID id auto-generation
- Status field validation against Literal set
- Frozen model enforcement
- Default values for started_at and completed_at
"""
import pytest
import uuid
from pydantic import ValidationError

from lattice.models.session import MappingSession


class TestMappingSessionValid:
    def test_creates_with_required_target_path(self):
        session = MappingSession(target_path="/repo")
        assert session.target_path == "/repo"

    def test_generates_uuid_id_by_default(self):
        session = MappingSession(target_path="/repo")
        # Should be a valid UUID string
        parsed = uuid.UUID(session.id)
        assert str(parsed) == session.id

    def test_default_status_is_pending(self):
        session = MappingSession(target_path="/repo")
        assert session.status == "pending"

    def test_default_completed_at_is_none(self):
        session = MappingSession(target_path="/repo")
        assert session.completed_at is None

    def test_default_started_at_is_set(self):
        from datetime import datetime

        session = MappingSession(target_path="/repo")
        assert isinstance(session.started_at, datetime)

    def test_all_valid_statuses(self):
        for status in ("pending", "running", "complete", "failed"):
            session = MappingSession(target_path="/repo", status=status)
            assert session.status == status

    def test_two_sessions_have_different_ids(self):
        s1 = MappingSession(target_path="/repo")
        s2 = MappingSession(target_path="/repo")
        assert s1.id != s2.id


class TestMappingSessionInvalid:
    def test_missing_target_path_raises(self):
        with pytest.raises(ValidationError):
            MappingSession()

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            MappingSession(target_path="/repo", status="unknown")

    def test_invalid_status_active_raises(self):
        with pytest.raises(ValidationError):
            MappingSession(target_path="/repo", status="active")


class TestMappingSessionFrozen:
    def test_assignment_raises_error(self):
        session = MappingSession(target_path="/repo")
        with pytest.raises(Exception):
            session.status = "running"

    def test_is_frozen_config(self):
        assert MappingSession.model_config.get("frozen") is True
