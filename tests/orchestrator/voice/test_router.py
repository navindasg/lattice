"""Tests for IntentRouter and RouteResult.

Tests cover:
    - RouteResult is a frozen Pydantic model
    - Each intent category dispatches to correct action
    - Multi-project confirmation guard
    - Single-project skip confirmation
    - Unrecognized intent with transcript echo and suggestion
    - All CC instance control intents (cc_command, cc_approve, cc_deny, etc.)
    - orchestrator_freeform dispatch
    - voice_request_id tracing in all CC/freeform results
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lattice.orchestrator.voice.models import IntentResult
from lattice.orchestrator.voice.router import IntentRouter, RouteResult


# ---------------------------------------------------------------------------
# RouteResult model tests
# ---------------------------------------------------------------------------


def test_route_result_is_frozen() -> None:
    """RouteResult must be an immutable Pydantic model."""
    result = RouteResult(success=True, action="task_enqueued")
    with pytest.raises(Exception):
        result.success = False  # type: ignore[misc]


def test_route_result_default_fields() -> None:
    """RouteResult has sensible defaults for detail and data."""
    result = RouteResult(success=True, action="task_enqueued")
    assert result.detail == ""
    assert result.data == {}


def test_route_result_full_construction() -> None:
    """RouteResult can be constructed with all fields."""
    result = RouteResult(
        success=False,
        action="unrecognized",
        detail="Unrecognized: foo",
        data={"transcript": "foo", "suggestion": "try rephrasing"},
    )
    assert result.success is False
    assert result.action == "unrecognized"
    assert result.data["transcript"] == "foo"


# ---------------------------------------------------------------------------
# task_dispatch tests
# ---------------------------------------------------------------------------


def test_task_dispatch_calls_enqueue() -> None:
    """task_dispatch intent calls TaskQueue.enqueue and returns task_enqueued."""
    mock_queue = MagicMock()
    mock_record = MagicMock()
    mock_record.task_id = "test-123"
    mock_queue.enqueue.return_value = mock_record

    router = IntentRouter(task_queue=mock_queue)
    intent = IntentResult(
        category="task_dispatch",
        transcript="fix the auth bug",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    mock_queue.enqueue.assert_called_once()
    assert result.success is True
    assert result.action == "task_enqueued"
    assert result.data["task_id"] == "test-123"


def test_task_dispatch_without_queue_returns_dry_run() -> None:
    """task_dispatch without a TaskQueue returns dry_run task_id."""
    router = IntentRouter()
    intent = IntentResult(
        category="task_dispatch",
        transcript="start working on auth",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    assert result.success is True
    assert result.action == "task_enqueued"
    assert result.data["task_id"] == "dry_run"


def test_task_dispatch_includes_transcript_in_detail() -> None:
    """task_dispatch detail string includes the transcript."""
    router = IntentRouter()
    intent = IntentResult(
        category="task_dispatch",
        transcript="implement login screen",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    assert "implement login screen" in result.detail


# ---------------------------------------------------------------------------
# status_query tests
# ---------------------------------------------------------------------------


def test_status_query_calls_get_all_instance_status() -> None:
    """status_query without instance_id calls get_all_instance_status."""
    mock_conn = MagicMock()
    mock_rows = [
        {
            "instance_id": "inst-001",
            "utilization_pct": 45.0,
            "bytes_sent": 100,
            "bytes_received": 200,
            "compaction_count": 1,
            "last_updated": "2026-01-01T00:00:00Z",
        }
    ]

    with patch(
        "lattice.orchestrator.voice.router.get_all_instance_status",
        return_value=mock_rows,
    ) as mock_all:
        router = IntentRouter(db_conn=mock_conn)
        intent = IntentResult(
            category="status_query",
            transcript="what's the status",
            confidence=0.9,
        )

        result = router.dispatch(intent)

    mock_all.assert_called_once_with(mock_conn)
    assert result.success is True
    assert result.action == "status_returned"
    assert result.data["instances"] == mock_rows


def test_status_query_with_instance_id_calls_get_instance_status() -> None:
    """status_query with instance_id extracted calls get_instance_status(instance_id)."""
    mock_conn = MagicMock()
    mock_status = {
        "instance_id": "abc",
        "utilization_pct": 30.0,
        "bytes_sent": 50,
        "bytes_received": 100,
        "compaction_count": 0,
        "last_updated": "2026-01-01T00:00:00Z",
    }

    with patch(
        "lattice.orchestrator.voice.router.get_instance_status",
        return_value=mock_status,
    ) as mock_single:
        router = IntentRouter(db_conn=mock_conn)
        intent = IntentResult(
            category="status_query",
            transcript="show me instance abc",
            confidence=0.9,
            extracted={"instance_id": "abc"},
        )

        result = router.dispatch(intent)

    mock_single.assert_called_once_with(mock_conn, "abc")
    assert result.success is True
    assert result.action == "status_returned"
    assert result.data["instances"] == [mock_status]


def test_status_query_without_db_conn_returns_empty() -> None:
    """status_query without a db_conn returns empty instances list."""
    router = IntentRouter()
    intent = IntentResult(
        category="status_query",
        transcript="what's the status",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    assert result.success is True
    assert result.action == "status_returned"
    assert result.data["instances"] == []


# ---------------------------------------------------------------------------
# mapper_command tests
# ---------------------------------------------------------------------------


def test_mapper_command_returns_mapper_dispatched() -> None:
    """mapper_command intent returns mapper_dispatched action."""
    router = IntentRouter()
    intent = IntentResult(
        category="mapper_command",
        transcript="map the auth directory",
        confidence=0.9,
        extracted={"target": "auth"},
    )

    result = router.dispatch(intent)

    assert result.success is True
    assert result.action == "mapper_dispatched"
    assert "auth" in result.detail
    assert result.data["target"] == "auth"
    assert "command" in result.data


def test_mapper_command_init_subcommand() -> None:
    """'init' in transcript routes to map:init subcommand."""
    router = IntentRouter()
    intent = IntentResult(
        category="mapper_command",
        transcript="map init on src/auth",
        confidence=0.9,
        extracted={"target": "src/auth"},
    )

    result = router.dispatch(intent)

    assert result.data["command"] == "map:init"


def test_mapper_command_status_subcommand() -> None:
    """'status' in transcript routes to map:status subcommand."""
    router = IntentRouter()
    intent = IntentResult(
        category="mapper_command",
        transcript="map status check",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    assert result.data["command"] == "map:status"


def test_mapper_command_gaps_subcommand() -> None:
    """'gap' in transcript routes to map:gaps subcommand."""
    router = IntentRouter()
    intent = IntentResult(
        category="mapper_command",
        transcript="map gaps for auth",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    assert result.data["command"] == "map:gaps"


def test_mapper_command_doc_subcommand() -> None:
    """'doc' in transcript routes to map:doc subcommand."""
    router = IntentRouter()
    intent = IntentResult(
        category="mapper_command",
        transcript="map document the project",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    assert result.data["command"] == "map:doc"


def test_mapper_command_default_target() -> None:
    """mapper_command with no target extracted defaults to '.'."""
    router = IntentRouter()
    intent = IntentResult(
        category="mapper_command",
        transcript="map the project",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    assert result.data["target"] == "."


# ---------------------------------------------------------------------------
# context_injection tests
# ---------------------------------------------------------------------------


def test_context_injection_returns_context_injected() -> None:
    """context_injection intent returns context_injected action."""
    router = IntentRouter()
    intent = IntentResult(
        category="context_injection",
        transcript="tell instance one about the auth refactor",
        confidence=0.9,
        extracted={"instance_id": "one"},
    )

    result = router.dispatch(intent)

    assert result.success is True
    assert result.action == "context_injected"
    assert result.data["instance_id"] == "one"
    assert "auth refactor" in result.data["content"]


def test_context_injection_broadcast_when_no_instance() -> None:
    """context_injection without instance_id sets instance_id to 'broadcast'."""
    router = IntentRouter()
    intent = IntentResult(
        category="context_injection",
        transcript="add context about the login flow",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    assert result.success is True
    assert result.action == "context_injected"
    assert result.data["instance_id"] == "broadcast"


# ---------------------------------------------------------------------------
# unrecognized tests
# ---------------------------------------------------------------------------


def test_unrecognized_returns_failure_with_transcript() -> None:
    """unrecognized intent returns success=False and echoes transcript."""
    router = IntentRouter()
    intent = IntentResult(
        category="unrecognized",
        transcript="asdf gibberish",
        confidence=0.0,
    )

    result = router.dispatch(intent)

    assert result.success is False
    assert result.action == "unrecognized"
    assert "asdf gibberish" in result.detail
    assert result.data["transcript"] == "asdf gibberish"


def test_unrecognized_includes_closest_match_suggestion() -> None:
    """unrecognized intent includes a suggestion in detail and data."""
    router = IntentRouter()
    intent = IntentResult(
        category="unrecognized",
        transcript="asdf gibberish",
        confidence=0.0,
    )

    result = router.dispatch(intent)

    assert "suggestion" in result.data
    assert result.data["suggestion"]  # non-empty


def test_unrecognized_suggests_status_for_status_like() -> None:
    """Unrecognized with 'status' keyword suggests status command."""
    router = IntentRouter()
    intent = IntentResult(
        category="unrecognized",
        transcript="show me the current status please",
        confidence=0.0,
    )

    result = router.dispatch(intent)

    assert "status" in result.data["suggestion"].lower()


def test_unrecognized_suggests_map_for_map_like() -> None:
    """Unrecognized with 'map' keyword suggests map command."""
    router = IntentRouter()
    intent = IntentResult(
        category="unrecognized",
        transcript="map something",
        confidence=0.0,
    )

    result = router.dispatch(intent)

    assert "map" in result.data["suggestion"].lower()


# ---------------------------------------------------------------------------
# Multi-project confirmation guard tests
# ---------------------------------------------------------------------------


def test_task_dispatch_multiple_projects_requires_confirmation() -> None:
    """task_dispatch with multiple active projects and no project specified returns confirmation_required."""
    mock_queue = MagicMock()
    router = IntentRouter(
        task_queue=mock_queue,
        active_projects=["projectA", "projectB"],
    )
    intent = IntentResult(
        category="task_dispatch",
        transcript="fix the auth bug",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    mock_queue.enqueue.assert_not_called()
    assert result.success is False
    assert result.action == "confirmation_required"
    assert "projectA" in result.detail
    assert "projectB" in result.detail


def test_task_dispatch_multiple_projects_with_project_specified_skips_confirmation() -> None:
    """task_dispatch with multiple projects but project in extracted skips confirmation."""
    mock_queue = MagicMock()
    mock_record = MagicMock()
    mock_record.task_id = "task-456"
    mock_queue.enqueue.return_value = mock_record

    router = IntentRouter(
        task_queue=mock_queue,
        active_projects=["projectA", "projectB"],
    )
    intent = IntentResult(
        category="task_dispatch",
        transcript="fix the auth bug in projectA",
        confidence=0.9,
        extracted={"project": "projectA"},
    )

    result = router.dispatch(intent)

    mock_queue.enqueue.assert_called_once()
    assert result.success is True
    assert result.action == "task_enqueued"


def test_task_dispatch_single_project_skips_confirmation() -> None:
    """task_dispatch with a single active project skips confirmation guard."""
    mock_queue = MagicMock()
    mock_record = MagicMock()
    mock_record.task_id = "task-789"
    mock_queue.enqueue.return_value = mock_record

    router = IntentRouter(
        task_queue=mock_queue,
        active_projects=["onlyProject"],
    )
    intent = IntentResult(
        category="task_dispatch",
        transcript="fix the auth bug",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    mock_queue.enqueue.assert_called_once()
    assert result.success is True
    assert result.action == "task_enqueued"


def test_task_dispatch_no_projects_skips_confirmation() -> None:
    """task_dispatch with no active projects skips confirmation guard."""
    mock_queue = MagicMock()
    mock_record = MagicMock()
    mock_record.task_id = "task-000"
    mock_queue.enqueue.return_value = mock_record

    router = IntentRouter(task_queue=mock_queue)
    intent = IntentResult(
        category="task_dispatch",
        transcript="fix the auth bug",
        confidence=0.9,
    )

    result = router.dispatch(intent)

    mock_queue.enqueue.assert_called_once()
    assert result.success is True


# ---------------------------------------------------------------------------
# external_fetch tests
# ---------------------------------------------------------------------------


class TestExternalFetch:
    def test_external_fetch_with_registry_returns_success(self) -> None:
        """external_fetch intent with connector_registry returns success=True."""
        mock_registry = MagicMock()

        router = IntentRouter(connector_registry=mock_registry)
        intent = IntentResult(
            category="external_fetch",
            transcript="look up SAML spec",
            confidence=0.9,
            extracted={"query": "SAML spec"},
        )

        result = router.dispatch(intent)

        assert result.success is True

    def test_external_fetch_without_registry_returns_unavailable(self) -> None:
        """external_fetch without connector_registry returns external_fetch_unavailable."""
        router = IntentRouter()
        intent = IntentResult(
            category="external_fetch",
            transcript="look up SAML spec",
            confidence=0.9,
            extracted={"query": "SAML spec"},
        )

        result = router.dispatch(intent)

        assert result.success is False
        assert result.action == "external_fetch_unavailable"

    def test_external_fetch_action_is_external_fetch_pending(self) -> None:
        """external_fetch with registry returns action='external_fetch_pending'."""
        mock_registry = MagicMock()

        router = IntentRouter(connector_registry=mock_registry)
        intent = IntentResult(
            category="external_fetch",
            transcript="look up python asyncio",
            confidence=0.9,
            extracted={"query": "python asyncio"},
        )

        result = router.dispatch(intent)

        assert result.action == "external_fetch_pending"

    def test_external_fetch_data_contains_connector_and_query(self) -> None:
        """external_fetch result data contains 'connector' and 'query' keys."""
        mock_registry = MagicMock()

        router = IntentRouter(connector_registry=mock_registry)
        intent = IntentResult(
            category="external_fetch",
            transcript="look up asyncio tutorial",
            confidence=0.9,
            extracted={"query": "asyncio tutorial"},
        )

        result = router.dispatch(intent)

        assert "connector" in result.data
        assert "query" in result.data
        assert result.data["query"] == "asyncio tutorial"

    def test_external_fetch_routes_github_by_keyword(self) -> None:
        """'github' keyword in transcript routes to github connector."""
        mock_registry = MagicMock()

        router = IntentRouter(connector_registry=mock_registry)
        intent = IntentResult(
            category="external_fetch",
            transcript="check the github issues",
            confidence=0.9,
        )

        result = router.dispatch(intent)

        assert result.data["connector"] == "github"

    def test_external_fetch_routes_mattermost_by_keyword(self) -> None:
        """'mattermost' keyword in transcript routes to mattermost connector."""
        mock_registry = MagicMock()

        router = IntentRouter(connector_registry=mock_registry)
        intent = IntentResult(
            category="external_fetch",
            transcript="check the mattermost channel",
            confidence=0.9,
        )

        result = router.dispatch(intent)

        assert result.data["connector"] == "mattermost"

    def test_external_fetch_defaults_to_tavily_for_web_search(self) -> None:
        """Generic 'look up X' routes to tavily connector by default."""
        mock_registry = MagicMock()

        router = IntentRouter(connector_registry=mock_registry)
        intent = IntentResult(
            category="external_fetch",
            transcript="look up SAML spec",
            confidence=0.9,
            extracted={"query": "SAML spec"},
        )

        result = router.dispatch(intent)

        assert result.data["connector"] == "tavily"


# ---------------------------------------------------------------------------
# mapper_command with subprocess (Phase 14 NDJSON dispatch tests)
# ---------------------------------------------------------------------------


def _make_mock_proc(returncode=None):
    """Create a mock asyncio.subprocess.Process with given returncode."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    return proc


def test_mapper_dispatch_with_subprocess_returns_pending() -> None:
    """IntentRouter._dispatch_mapper returns mapper_dispatch_pending when mapper subprocess is live."""
    mock_proc = _make_mock_proc(returncode=None)  # returncode=None means process is alive

    router = IntentRouter(
        active_projects=["myapp"],
        mapper_processes={"myapp": mock_proc},
    )
    intent = IntentResult(
        category="mapper_command",
        transcript="map the auth directory",
        confidence=0.9,
        extracted={"target": "auth", "project": "myapp"},
    )

    result = router.dispatch(intent)

    assert result.success is True
    assert result.action == "mapper_dispatch_pending"
    assert result.data["command"].startswith("map:")
    assert result.data["project"] == "myapp"
    assert result.data["target"] == "auth"


def test_mapper_dispatch_multiproject_guard() -> None:
    """IntentRouter._dispatch_mapper returns confirmation_required when multiple projects and no project specified."""
    mock_proc_a = _make_mock_proc(returncode=None)
    mock_proc_b = _make_mock_proc(returncode=None)

    router = IntentRouter(
        active_projects=["projectA", "projectB"],
        mapper_processes={"projectA": mock_proc_a, "projectB": mock_proc_b},
    )
    intent = IntentResult(
        category="mapper_command",
        transcript="map the auth directory",
        confidence=0.9,
        extracted={"target": "auth"},  # no "project" key
    )

    result = router.dispatch(intent)

    assert result.success is False
    assert result.action == "confirmation_required"
    assert "projectA" in result.detail
    assert "projectB" in result.detail


def test_mapper_dispatch_fallback_no_subprocess() -> None:
    """IntentRouter._dispatch_mapper returns mapper_dispatched (fallback) when no subprocess available."""
    router = IntentRouter(
        active_projects=["myapp"],
        mapper_processes={},  # empty — no subprocess
    )
    intent = IntentResult(
        category="mapper_command",
        transcript="map the auth directory",
        confidence=0.9,
        extracted={"target": "auth", "project": "myapp"},
    )

    result = router.dispatch(intent)

    assert result.success is True
    assert result.action == "mapper_dispatched"
    assert result.data["command"].startswith("map:")


def test_mapper_dispatch_dead_subprocess_falls_back() -> None:
    """IntentRouter._dispatch_mapper falls back to mapper_dispatched when subprocess returncode is set (dead)."""
    dead_proc = _make_mock_proc(returncode=1)  # returncode != None means dead

    router = IntentRouter(
        active_projects=["myapp"],
        mapper_processes={"myapp": dead_proc},
    )
    intent = IntentResult(
        category="mapper_command",
        transcript="map the auth directory",
        confidence=0.9,
        extracted={"target": "auth", "project": "myapp"},
    )

    result = router.dispatch(intent)

    # Dead process — should fall back to non-subprocess behavior
    assert result.action == "mapper_dispatched"


def test_mapper_dispatch_single_project_auto_resolved() -> None:
    """IntentRouter._dispatch_mapper auto-resolves project when single active_projects entry."""
    mock_proc = _make_mock_proc(returncode=None)

    router = IntentRouter(
        active_projects=["onlyproject"],
        mapper_processes={"onlyproject": mock_proc},
    )
    intent = IntentResult(
        category="mapper_command",
        transcript="map status check",
        confidence=0.9,
        extracted={},  # no target, no project
    )

    result = router.dispatch(intent)

    assert result.success is True
    assert result.action == "mapper_dispatch_pending"
    assert result.data["project"] == "onlyproject"
    assert result.data["command"] == "map:status"


# ---------------------------------------------------------------------------
# CC command dispatch tests
# ---------------------------------------------------------------------------


class TestCCCommandDispatch:
    def test_cc_command_dispatched_success(self) -> None:
        """cc_command intent returns cc_command_dispatched with instance and message."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_command",
            transcript="tell 3 to fix the auth bug",
            confidence=0.9,
            extracted={"instance": "3", "message": "fix the auth bug"},
        )

        result = router.dispatch(intent)

        assert result.success is True
        assert result.action == "cc_command_dispatched"
        assert result.data["instance"] == "3"
        assert result.data["message"] == "fix the auth bug"
        assert result.data["intent"] == "cc_command"

    def test_cc_command_has_voice_request_id(self) -> None:
        """cc_command result includes voice_request_id for tracing."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_command",
            transcript="tell 1 to update tests",
            confidence=0.9,
            extracted={"instance": "1", "message": "update tests"},
        )

        result = router.dispatch(intent)

        assert "voice_request_id" in result.data
        assert len(result.data["voice_request_id"]) == 36  # UUID4 format

    def test_cc_command_detail_includes_instance_and_message(self) -> None:
        """cc_command detail string includes instance number and message."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_command",
            transcript="tell 5 to refactor",
            confidence=0.9,
            extracted={"instance": "5", "message": "refactor"},
        )

        result = router.dispatch(intent)

        assert "5" in result.detail
        assert "refactor" in result.detail


# ---------------------------------------------------------------------------
# CC approve dispatch tests
# ---------------------------------------------------------------------------


class TestCCApproveDispatch:
    def test_cc_approve_dispatched_success(self) -> None:
        """cc_approve intent returns cc_approve_dispatched."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_approve",
            transcript="4 approved",
            confidence=0.9,
            extracted={"instance": "4"},
        )

        result = router.dispatch(intent)

        assert result.success is True
        assert result.action == "cc_approve_dispatched"
        assert result.data["instance"] == "4"
        assert result.data["intent"] == "cc_approve"

    def test_cc_approve_has_voice_request_id(self) -> None:
        """cc_approve result includes voice_request_id."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_approve",
            transcript="approve 3",
            confidence=0.9,
            extracted={"instance": "3"},
        )

        result = router.dispatch(intent)

        assert "voice_request_id" in result.data


# ---------------------------------------------------------------------------
# CC deny dispatch tests
# ---------------------------------------------------------------------------


class TestCCDenyDispatch:
    def test_cc_deny_dispatched_success(self) -> None:
        """cc_deny intent returns cc_deny_dispatched."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_deny",
            transcript="4 denied",
            confidence=0.9,
            extracted={"instance": "4"},
        )

        result = router.dispatch(intent)

        assert result.success is True
        assert result.action == "cc_deny_dispatched"
        assert result.data["instance"] == "4"
        assert result.data["intent"] == "cc_deny"

    def test_cc_deny_has_voice_request_id(self) -> None:
        """cc_deny result includes voice_request_id."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_deny",
            transcript="deny 7",
            confidence=0.9,
            extracted={"instance": "7"},
        )

        result = router.dispatch(intent)

        assert "voice_request_id" in result.data


# ---------------------------------------------------------------------------
# CC deny_redirect dispatch tests
# ---------------------------------------------------------------------------


class TestCCDenyRedirectDispatch:
    def test_cc_deny_redirect_dispatched_success(self) -> None:
        """cc_deny_redirect intent returns cc_deny_redirect_dispatched."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_deny_redirect",
            transcript="6 denied, tell it to use AWS",
            confidence=0.9,
            extracted={"instance": "6", "message": "use AWS"},
        )

        result = router.dispatch(intent)

        assert result.success is True
        assert result.action == "cc_deny_redirect_dispatched"
        assert result.data["instance"] == "6"
        assert result.data["message"] == "use AWS"
        assert result.data["intent"] == "cc_deny_redirect"

    def test_cc_deny_redirect_has_voice_request_id(self) -> None:
        """cc_deny_redirect result includes voice_request_id."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_deny_redirect",
            transcript="deny 3, use PostgreSQL",
            confidence=0.9,
            extracted={"instance": "3", "message": "use PostgreSQL"},
        )

        result = router.dispatch(intent)

        assert "voice_request_id" in result.data

    def test_cc_deny_redirect_detail_includes_redirect_message(self) -> None:
        """cc_deny_redirect detail includes the redirect message."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_deny_redirect",
            transcript="deny 2, switch to async",
            confidence=0.9,
            extracted={"instance": "2", "message": "switch to async"},
        )

        result = router.dispatch(intent)

        assert "switch to async" in result.detail
        assert "2" in result.detail


# ---------------------------------------------------------------------------
# CC status dispatch tests
# ---------------------------------------------------------------------------


class TestCCStatusDispatch:
    def test_cc_status_dispatched_success(self) -> None:
        """cc_status intent returns cc_status_dispatched."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_status",
            transcript="what's 2 doing",
            confidence=0.9,
            extracted={"instance": "2"},
        )

        result = router.dispatch(intent)

        assert result.success is True
        assert result.action == "cc_status_dispatched"
        assert result.data["instance"] == "2"
        assert result.data["intent"] == "cc_status"

    def test_cc_status_has_voice_request_id(self) -> None:
        """cc_status result includes voice_request_id."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_status",
            transcript="status 5",
            confidence=0.9,
            extracted={"instance": "5"},
        )

        result = router.dispatch(intent)

        assert "voice_request_id" in result.data


# ---------------------------------------------------------------------------
# CC interrupt dispatch tests
# ---------------------------------------------------------------------------


class TestCCInterruptDispatch:
    def test_cc_interrupt_dispatched_success(self) -> None:
        """cc_interrupt intent returns cc_interrupt_dispatched."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_interrupt",
            transcript="stop 5",
            confidence=0.9,
            extracted={"instance": "5"},
        )

        result = router.dispatch(intent)

        assert result.success is True
        assert result.action == "cc_interrupt_dispatched"
        assert result.data["instance"] == "5"
        assert result.data["intent"] == "cc_interrupt"

    def test_cc_interrupt_has_voice_request_id(self) -> None:
        """cc_interrupt result includes voice_request_id."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_interrupt",
            transcript="kill 7",
            confidence=0.9,
            extracted={"instance": "7"},
        )

        result = router.dispatch(intent)

        assert "voice_request_id" in result.data


# ---------------------------------------------------------------------------
# orchestrator_freeform dispatch tests
# ---------------------------------------------------------------------------


class TestOrchestratorFreeformDispatch:
    def test_freeform_dispatched_success(self) -> None:
        """orchestrator_freeform returns orchestrator_freeform_dispatched."""
        router = IntentRouter()
        intent = IntentResult(
            category="orchestrator_freeform",
            transcript="we need to ship auth by Friday",
            confidence=0.5,
            extracted={"message": "we need to ship auth by Friday"},
        )

        result = router.dispatch(intent)

        assert result.success is True
        assert result.action == "orchestrator_freeform_dispatched"
        assert result.data["intent"] == "orchestrator_freeform"
        assert result.data["message"] == "we need to ship auth by Friday"

    def test_freeform_has_voice_request_id(self) -> None:
        """orchestrator_freeform result includes voice_request_id."""
        router = IntentRouter()
        intent = IntentResult(
            category="orchestrator_freeform",
            transcript="hello testing",
            confidence=0.5,
            extracted={"message": "hello testing"},
        )

        result = router.dispatch(intent)

        assert "voice_request_id" in result.data

    def test_freeform_detail_includes_message(self) -> None:
        """orchestrator_freeform detail includes the full message."""
        router = IntentRouter()
        intent = IntentResult(
            category="orchestrator_freeform",
            transcript="the client wants dark mode",
            confidence=0.5,
            extracted={"message": "the client wants dark mode"},
        )

        result = router.dispatch(intent)

        assert "the client wants dark mode" in result.detail


# ---------------------------------------------------------------------------
# voice_request_id uniqueness across dispatches
# ---------------------------------------------------------------------------


class TestVoiceRequestIdUniqueness:
    def test_different_dispatches_get_unique_ids(self) -> None:
        """Each dispatch call generates a unique voice_request_id."""
        router = IntentRouter()
        intent = IntentResult(
            category="cc_approve",
            transcript="approve 1",
            confidence=0.9,
            extracted={"instance": "1"},
        )

        result1 = router.dispatch(intent)
        result2 = router.dispatch(intent)

        assert result1.data["voice_request_id"] != result2.data["voice_request_id"]
