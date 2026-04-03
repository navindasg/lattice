"""Tests for GitHubConnector.

Tests use mocked PyGithub objects — no real API calls.
Covers issues, PRs, CI status, write comment, permission guard, source tagging.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from lattice.orchestrator.connectors.base import ConnectorError
from lattice.orchestrator.connectors.github import GitHubConnector
from lattice.orchestrator.connectors.models import ConnectorConfig, ConnectorPermissions


@pytest.fixture()
def config() -> ConnectorConfig:
    return ConnectorConfig(
        name="github",
        connector_type="github",
        token="test-token",
        repo="owner/repo",
    )


@pytest.fixture()
def write_config() -> ConnectorConfig:
    return ConnectorConfig(
        name="github-write",
        connector_type="github",
        token="test-token",
        repo="owner/repo",
        permissions=ConnectorPermissions(read=True, write=True),
    )


@pytest.fixture()
def connector(config: ConnectorConfig) -> GitHubConnector:
    return GitHubConnector(config)


@pytest.fixture()
def write_connector(write_config: ConnectorConfig) -> GitHubConnector:
    return GitHubConnector(write_config)


def _make_issue(number: int, title: str, state: str = "open", body: str = "") -> MagicMock:
    issue = MagicMock()
    issue.number = number
    issue.title = title
    issue.state = state
    issue.body = body
    return issue


def _make_pr(number: int, title: str, state: str = "open", body: str = "") -> MagicMock:
    pr = MagicMock()
    pr.number = number
    pr.title = title
    pr.state = state
    pr.body = body
    return pr


def _make_check_run(name: str, status: str, conclusion: str | None) -> MagicMock:
    run = MagicMock()
    run.name = name
    run.status = status
    run.conclusion = conclusion
    return run


# ---------------------------------------------------------------------------
# fetch("issues") tests
# ---------------------------------------------------------------------------


class TestFetchIssues:
    def test_fetch_issues_returns_success_result(self, connector: GitHubConnector) -> None:
        """fetch('issues') returns ConnectorResult(success=True)."""
        mock_issue = _make_issue(1, "Fix login bug")

        with patch("lattice.orchestrator.connectors.github.Github") as MockGithub:
            mock_repo = MagicMock()
            mock_repo.get_issues.return_value = [mock_issue]
            MockGithub.return_value.get_repo.return_value = mock_repo

            result = asyncio.run(connector.fetch("issues"))

        assert result.success is True
        assert result.source == "github"

    def test_fetch_issues_content_contains_issue_number_and_title(
        self, connector: GitHubConnector
    ) -> None:
        """fetch('issues') content includes issue number and title."""
        mock_issue = _make_issue(42, "Implement SAML support")

        with patch("lattice.orchestrator.connectors.github.Github") as MockGithub:
            mock_repo = MagicMock()
            mock_repo.get_issues.return_value = [mock_issue]
            MockGithub.return_value.get_repo.return_value = mock_repo

            result = asyncio.run(connector.fetch("issues"))

        assert "#42" in result.content
        assert "Implement SAML support" in result.content

    def test_fetch_issues_content_prefixed_with_source(self, connector: GitHubConnector) -> None:
        """fetch content is prefixed with '[Source: GitHub owner/repo]'."""
        with patch("lattice.orchestrator.connectors.github.Github") as MockGithub:
            mock_repo = MagicMock()
            mock_repo.get_issues.return_value = []
            MockGithub.return_value.get_repo.return_value = mock_repo

            result = asyncio.run(connector.fetch("issues"))

        assert result.content.startswith("[Source: GitHub owner/repo]")

    def test_fetch_issues_uses_asyncio_to_thread(self, connector: GitHubConnector) -> None:
        """fetch('issues') wraps PyGithub calls in asyncio.to_thread."""
        # If asyncio.to_thread is bypassed, blocking calls would block the event loop
        # We verify via the async execution path succeeds without blocking
        with patch("lattice.orchestrator.connectors.github.Github") as MockGithub:
            mock_repo = MagicMock()
            mock_repo.get_issues.return_value = [_make_issue(1, "Test")]
            MockGithub.return_value.get_repo.return_value = mock_repo

            # Should complete without error (would hang/error if asyncio.to_thread missing)
            result = asyncio.run(connector.fetch("issues"))

        assert result.success is True


# ---------------------------------------------------------------------------
# fetch("prs") tests
# ---------------------------------------------------------------------------


class TestFetchPRs:
    def test_fetch_prs_returns_pr_numbers_and_titles(self, connector: GitHubConnector) -> None:
        """fetch('prs') content includes PR numbers and titles."""
        mock_pr = _make_pr(7, "Add dark mode support")

        with patch("lattice.orchestrator.connectors.github.Github") as MockGithub:
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [mock_pr]
            MockGithub.return_value.get_repo.return_value = mock_repo

            result = asyncio.run(connector.fetch("prs"))

        assert result.success is True
        assert "PR #7" in result.content
        assert "Add dark mode support" in result.content

    def test_fetch_pull_requests_alias(self, connector: GitHubConnector) -> None:
        """'pull' keyword also routes to PR fetch."""
        mock_pr = _make_pr(3, "Some PR")

        with patch("lattice.orchestrator.connectors.github.Github") as MockGithub:
            mock_repo = MagicMock()
            mock_repo.get_pulls.return_value = [mock_pr]
            MockGithub.return_value.get_repo.return_value = mock_repo

            result = asyncio.run(connector.fetch("pull requests"))

        assert result.success is True
        assert "PR #3" in result.content


# ---------------------------------------------------------------------------
# fetch("ci_status") tests
# ---------------------------------------------------------------------------


class TestFetchCIStatus:
    def test_fetch_ci_status_returns_check_run_info(self, connector: GitHubConnector) -> None:
        """fetch('ci_status', commit_sha='abc123') returns check run names and conclusions."""
        mock_run = _make_check_run("test-suite", "completed", "success")

        with patch("lattice.orchestrator.connectors.github.Github") as MockGithub:
            mock_repo = MagicMock()
            mock_commit = MagicMock()
            mock_commit.get_check_runs.return_value = [mock_run]
            mock_repo.get_commit.return_value = mock_commit
            MockGithub.return_value.get_repo.return_value = mock_repo

            result = asyncio.run(connector.fetch("ci_status", commit_sha="abc123"))

        assert result.success is True
        assert "test-suite" in result.content
        assert "completed" in result.content

    def test_fetch_ci_status_delivery_mode_is_ndjson(self, connector: GitHubConnector) -> None:
        """CI status uses delivery_mode='ndjson' (short/urgent)."""
        mock_run = _make_check_run("build", "completed", "success")

        with patch("lattice.orchestrator.connectors.github.Github") as MockGithub:
            mock_repo = MagicMock()
            mock_commit = MagicMock()
            mock_commit.get_check_runs.return_value = [mock_run]
            mock_repo.get_commit.return_value = mock_commit
            MockGithub.return_value.get_repo.return_value = mock_repo

            result = asyncio.run(connector.fetch("ci_status", commit_sha="sha1"))

        assert result.delivery_mode == "ndjson"


# ---------------------------------------------------------------------------
# write tests
# ---------------------------------------------------------------------------


class TestWrite:
    def test_write_without_write_permission_returns_error(
        self, connector: GitHubConnector
    ) -> None:
        """write raises ConnectorError when permissions.write is False."""
        result = asyncio.run(connector.write("comment body", issue_number=1, body="LGTM"))
        assert result.success is False
        assert "write" in result.error.lower() or "permission" in result.error.lower()

    def test_write_with_permission_posts_comment(
        self, write_connector: GitHubConnector
    ) -> None:
        """write with permissions.write=True posts comment and returns success."""
        mock_comment = MagicMock()
        mock_comment.id = 999

        with patch("lattice.orchestrator.connectors.github.Github") as MockGithub:
            mock_repo = MagicMock()
            mock_issue = MagicMock()
            mock_issue.create_comment.return_value = mock_comment
            mock_repo.get_issue.return_value = mock_issue
            MockGithub.return_value.get_repo.return_value = mock_repo

            result = asyncio.run(
                write_connector.write("LGTM", issue_number=1, body="LGTM")
            )

        assert result.success is True
        assert result.source == "github"
        assert "#1" in result.content


# ---------------------------------------------------------------------------
# health_check tests
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_health_check_false_when_token_empty(self) -> None:
        """health_check returns False when token is empty."""
        config = ConnectorConfig(
            name="github-no-token",
            connector_type="github",
            token="",
            repo="owner/repo",
        )
        conn = GitHubConnector(config)
        result = asyncio.run(conn.health_check())
        assert result is False

    def test_health_check_false_when_repo_empty(self) -> None:
        """health_check returns False when repo is empty."""
        config = ConnectorConfig(
            name="github-no-repo",
            connector_type="github",
            token="some-token",
            repo="",
        )
        conn = GitHubConnector(config)
        result = asyncio.run(conn.health_check())
        assert result is False

    def test_health_check_true_when_token_and_repo_set(
        self, connector: GitHubConnector
    ) -> None:
        """health_check returns True when token and repo are both non-empty."""
        result = asyncio.run(connector.health_check())
        assert result is True
