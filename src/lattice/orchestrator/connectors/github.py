"""GitHubConnector: reads issues, PRs, and CI status from a configured repository.

Write (comment) operations are blocked unless permissions.write is explicitly True.

Design decisions:
- Lazy GitHub client initialization (_ensure_github) to avoid connection at import
- All PyGithub calls wrapped in asyncio.to_thread (PyGithub is sync-only)
- CI status delivery_mode='ndjson' (short/urgent), issues/PRs use 'soul_file'
- Write requires permissions.write=True (ConnectorConfig.permissions)
- Github imported at module level so tests can patch via
  'lattice.orchestrator.connectors.github.Github'
"""
from __future__ import annotations

import asyncio

import structlog

from lattice.orchestrator.connectors.base import BaseConnector, ConnectorError
from lattice.orchestrator.connectors.models import ConnectorConfig, ConnectorResult

log = structlog.get_logger(__name__)

# Module-level import so tests can patch it.
try:
    from github import Github  # type: ignore[import]
except ImportError:
    Github = None  # type: ignore[assignment,misc]


class GitHubConnector(BaseConnector):
    """GitHub connector for reading issues, PRs, and CI status.

    Supports optional write (comment) when permissions.write=True.
    All PyGithub operations are wrapped in asyncio.to_thread since PyGithub
    is a synchronous library.

    Args:
        config: ConnectorConfig with token and repo set.
                repo must be in 'owner/repo' format.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._github: object | None = None

    def _ensure_github(self) -> object:
        """Lazily create a Github client on first use.

        Returns:
            Github instance authenticated with self._config.token.
        """
        if self._github is None:
            if Github is None:
                raise ConnectorError("PyGithub is not installed")
            self._github = Github(self._config.token)
        return self._github

    def _source_prefix(self) -> str:
        return f"[Source: GitHub {self._config.repo}]"

    async def fetch(self, query: str, **kwargs: object) -> ConnectorResult:
        """Fetch GitHub data based on query keyword.

        Query routing:
        - 'issues' (default): open issues from configured repo
        - 'prs' or 'pull': open pull requests
        - 'ci_status': check runs for a commit (requires commit_sha kwarg)

        Args:
            query: Routing keyword ('issues', 'prs', 'pull', 'ci_status', or anything else).
            **kwargs: commit_sha (str) required when query='ci_status'.

        Returns:
            ConnectorResult with content prefixed by '[Source: GitHub owner/repo]'.

        Raises:
            ConnectorError: On GitHub API failure.
        """
        query_lower = query.lower()

        try:
            if query_lower.startswith("ci_status") or query_lower.startswith("ci"):
                return await self._fetch_ci_status(**kwargs)
            elif query_lower.startswith("prs") or "pull" in query_lower:
                return await self._fetch_prs()
            else:
                # Default: issues (covers 'issues' and any unrecognized query)
                return await self._fetch_issues()
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"GitHub fetch failed: {exc}") from exc

    async def _fetch_issues(self) -> ConnectorResult:
        """Fetch open issues from the configured repository."""
        gh = self._ensure_github()

        def _get_issues() -> list:
            repo = gh.get_repo(self._config.repo)  # type: ignore[attr-defined]
            return list(repo.get_issues(state="open"))[:10]

        issues = await asyncio.to_thread(_get_issues)

        lines = [self._source_prefix()]
        for issue in issues:
            body_snippet = (issue.body or "")[:200]
            lines.append(f"#{issue.number} {issue.title} ({issue.state})")
            if body_snippet:
                lines.append(f"   {body_snippet}")

        return ConnectorResult(
            success=True,
            source="github",
            content="\n".join(lines),
            delivery_mode="soul_file",
            metadata={"repo": self._config.repo, "query": "issues"},
        )

    async def _fetch_prs(self) -> ConnectorResult:
        """Fetch open pull requests from the configured repository."""
        gh = self._ensure_github()

        def _get_prs() -> list:
            repo = gh.get_repo(self._config.repo)  # type: ignore[attr-defined]
            return list(repo.get_pulls(state="open"))[:10]

        prs = await asyncio.to_thread(_get_prs)

        lines = [self._source_prefix()]
        for pr in prs:
            body_snippet = (pr.body or "")[:200]
            lines.append(f"PR #{pr.number} {pr.title} ({pr.state})")
            if body_snippet:
                lines.append(f"   {body_snippet}")

        return ConnectorResult(
            success=True,
            source="github",
            content="\n".join(lines),
            delivery_mode="soul_file",
            metadata={"repo": self._config.repo, "query": "prs"},
        )

    async def _fetch_ci_status(self, **kwargs: object) -> ConnectorResult:
        """Fetch check runs for a specific commit."""
        commit_sha = str(kwargs.get("commit_sha", ""))
        gh = self._ensure_github()

        def _get_check_runs() -> list:
            repo = gh.get_repo(self._config.repo)  # type: ignore[attr-defined]
            commit = repo.get_commit(commit_sha)
            return list(commit.get_check_runs())

        runs = await asyncio.to_thread(_get_check_runs)

        lines = [self._source_prefix()]
        for run in runs:
            lines.append(f"{run.name}: {run.status}/{run.conclusion}")

        return ConnectorResult(
            success=True,
            source="github",
            content="\n".join(lines),
            delivery_mode="ndjson",
            metadata={"repo": self._config.repo, "commit_sha": commit_sha},
        )

    async def write(self, content: str, **kwargs: object) -> ConnectorResult:
        """Post a comment on a GitHub issue.

        Requires permissions.write=True on the ConnectorConfig.

        Args:
            content: Ignored (use kwargs['body'] for the comment text).
            **kwargs:
                issue_number (int): Issue to comment on.
                body (str): Comment body text.

        Returns:
            ConnectorResult(success=True) on success, (success=False) if blocked.

        Raises:
            ConnectorError: On GitHub API failure.
        """
        if not self._config.permissions.write:
            return ConnectorResult(
                success=False,
                source="github",
                error="Write permission denied — set permissions.write=True in ConnectorConfig",
            )

        issue_number = int(kwargs.get("issue_number", 0))  # type: ignore[arg-type]
        body = str(kwargs.get("body", content))
        gh = self._ensure_github()

        try:
            def _create_comment() -> object:
                repo = gh.get_repo(self._config.repo)  # type: ignore[attr-defined]
                issue = repo.get_issue(issue_number)
                return issue.create_comment(body)

            await asyncio.to_thread(_create_comment)
        except Exception as exc:
            raise ConnectorError(f"GitHub write failed: {exc}") from exc

        log.info("github_comment_posted", repo=self._config.repo, issue_number=issue_number)

        return ConnectorResult(
            success=True,
            source="github",
            content=f"Comment posted on #{issue_number}",
            delivery_mode="ndjson",
        )

    async def health_check(self) -> bool:
        """Check if the connector has both a token and a repo configured.

        Returns:
            True if both token and repo are non-empty, False otherwise.
        """
        return bool(self._config.token and self._config.repo)
