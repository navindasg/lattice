"""Skeleton writer — writes test stubs to _test_stubs/ shadow path.

SkeletonWriter is stateless. Each call to write_stubs() is independent.

Stubs are written to:
    {agent_docs_root}/{directory}/_test_stubs/{stub_filename}

Python stubs are validated with compile() before writing.
TypeScript stubs are validated by checking for jest structure keywords.
Invalid stubs are logged and skipped — not written silently.
"""
from __future__ import annotations

from pathlib import Path

import structlog

from lattice.fleet.models import AgentResult

log = structlog.get_logger(__name__)


class SkeletonWriter:
    """Stateless writer for test stubs from AgentResult.test_stubs.

    Usage::

        writer = SkeletonWriter()
        paths = writer.write_stubs(agent_result, agent_docs_root)
    """

    def write_stubs(
        self,
        agent_result: AgentResult,
        agent_docs_root: Path,
    ) -> list[Path]:
        """Write test stubs to _test_stubs/ shadow directory.

        For each stub in agent_result.test_stubs, construct the shadow path
        and write the content after validation. Creates _test_stubs/ directory
        if it doesn't exist.

        Args:
            agent_result: AgentResult containing test_stubs list.
            agent_docs_root: Root of the .agent-docs shadow tree.

        Returns:
            List of paths to successfully written stub files.
        """
        if not agent_result.test_stubs:
            return []

        stubs_dir = agent_docs_root / agent_result.directory / "_test_stubs"
        stubs_dir.mkdir(parents=True, exist_ok=True)

        written_paths: list[Path] = []

        for stub in agent_result.test_stubs:
            stub_path_name = stub.get("path", "")
            content = stub.get("content", "")
            language = stub.get("language", "").lower()

            if not stub_path_name or not content:
                log.warning(
                    "skeleton_skip_empty_stub",
                    directory=agent_result.directory,
                    path=stub_path_name,
                )
                continue

            # Validate syntax before writing
            if language == "python":
                if not self._validate_python_stub(content, stub_path_name):
                    log.warning(
                        "skeleton_skip_invalid_python",
                        directory=agent_result.directory,
                        path=stub_path_name,
                    )
                    continue
            elif language in ("typescript", "javascript"):
                if not self._validate_typescript_stub(content, stub_path_name):
                    log.warning(
                        "skeleton_skip_invalid_typescript",
                        directory=agent_result.directory,
                        path=stub_path_name,
                    )
                    continue

            # Write the stub
            stub_file = stubs_dir / stub_path_name
            stub_file.parent.mkdir(parents=True, exist_ok=True)
            stub_file.write_text(content, encoding="utf-8")

            log.info(
                "skeleton_wrote_stub",
                directory=agent_result.directory,
                path=str(stub_file),
                language=language,
            )
            written_paths.append(stub_file)

        return written_paths

    def _validate_python_stub(self, content: str, filename: str) -> bool:
        """Validate Python stub syntax via compile().

        Args:
            content: Python source code as string.
            filename: Filename for error context.

        Returns:
            True if syntax is valid, False otherwise.
        """
        try:
            compile(content, filename, "exec")
            return True
        except SyntaxError:
            return False

    def _validate_typescript_stub(self, content: str, filename: str) -> bool:
        """Validate TypeScript/JavaScript stub has basic jest structure.

        Checks for presence of common jest keywords: describe, it, or test.

        Args:
            content: TypeScript/JavaScript source code as string.
            filename: Filename for error context.

        Returns:
            True if basic jest structure is detected, False otherwise.
        """
        return any(keyword in content for keyword in ("describe", "it(", "test("))
