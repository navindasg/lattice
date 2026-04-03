"""TestDiscovery — test file discovery engine for Python and TypeScript/JavaScript.

Finds pytest-convention Python test files (test_*.py, *_test.py) and
jest-convention TS/JS test files (*.test.ts, *.spec.js, __tests__/*.ts etc.)
using recursive directory traversal.

Excluded directories mirror the _SKIP_DIRS frozenset in lattice.cli.commands.
conftest.py files are intentionally excluded — they define fixtures, not tests.
"""
from pathlib import Path

_SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__",
    "node_modules",
    ".git",
    ".agent-docs",
    ".venv",
    "venv",
    ".tox",
    "dist",
    "build",
    "htmlcov",
    ".mypy_cache",
    ".pytest_cache",
})

_PYTHON_TEST_EXTENSIONS: frozenset[str] = frozenset({".py"})
_TS_JS_EXTENSIONS: frozenset[str] = frozenset({".ts", ".tsx", ".js", ".jsx"})


class TestDiscovery:
    """Discovers test files in a project using framework conventions.

    Supports:
    - pytest: test_*.py and *_test.py patterns (excluding conftest.py)
    - jest: *.test.{ts,tsx,js,jsx}, *.spec.{ts,tsx,js,jsx}, and files in
      __tests__/ directories

    Args:
        project_root: Root directory to search for test files.
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    def discover(self) -> list[Path]:
        """Find all test files under project_root.

        Returns:
            Sorted list of Path objects pointing to discovered test files.
            Excludes conftest.py and directories in _SKIP_DIRS.
        """
        results: list[Path] = []

        for path in self._walk():
            if self._is_test_file(path):
                results.append(path)

        return sorted(results)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _walk(self):
        """Yield all files under project_root, skipping excluded directories."""
        try:
            for entry in self._project_root.rglob("*"):
                # Skip if any ancestor directory is in SKIP_DIRS
                if self._is_in_skip_dir(entry):
                    continue
                if entry.is_file():
                    yield entry
        except PermissionError:
            pass

    def _is_in_skip_dir(self, path: Path) -> bool:
        """Return True if path is inside any excluded directory."""
        # Check every part of the path relative to project_root
        try:
            relative = path.relative_to(self._project_root)
        except ValueError:
            return False

        for part in relative.parts[:-1]:  # exclude the filename itself
            if part in _SKIP_DIRS:
                return True
        return False

    def _is_test_file(self, path: Path) -> bool:
        """Return True if the file matches pytest or jest test conventions."""
        suffix = path.suffix
        stem = path.stem  # e.g. "test_auth" or "api.test"
        name = path.name  # e.g. "test_auth.py" or "api.test.ts"

        # --- Python (pytest) ---
        if suffix in _PYTHON_TEST_EXTENSIONS:
            # Exclude conftest.py explicitly
            if name == "conftest.py":
                return False
            # test_*.py and *_test.py
            if stem.startswith("test_") or stem.endswith("_test"):
                return True
            return False

        # --- TypeScript / JavaScript (jest) ---
        if suffix in _TS_JS_EXTENSIONS:
            # Files in __tests__/ directories
            try:
                relative = path.relative_to(self._project_root)
            except ValueError:
                relative = path
            if "__tests__" in relative.parts:
                return True

            # *.test.ts, *.spec.ts, *.test.js, etc.
            # stem here is e.g. "api.test" for "api.test.ts"
            # We check if the stem ends with ".test" or ".spec"
            if stem.endswith(".test") or stem.endswith(".spec"):
                return True

            return False

        return False
