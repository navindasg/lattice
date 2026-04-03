"""TestClassifier — test type classification engine (TC-02).

Classifies discovered test files into unit, integration, or e2e using a
two-stage heuristic:

Stage 1 — Directory convention (authoritative):
    tests/unit/, tests/integration/, tests/e2e/ (and common variants) indicate
    the test type directly. This takes priority over import analysis.

Stage 2 — Import analysis fallback (only if no directory match):
    Analyzes imports via PythonAdapter / TypeScriptAdapter:
    - Infrastructure signals (sqlalchemy, playwright, etc.) → integration/e2e
    - 2+ internal source module imports → integration
    - 0–1 internal source module imports → unit

Both stages always populate source_modules with resolved internal import paths.
"""
from pathlib import Path
from typing import Literal

from lattice.adapters.python_adapter import PythonAdapter
from lattice.graph.builder import DependencyGraphBuilder
from lattice.models.coverage import TestFile, TestType

# Directory name sets for convention-based classification
_E2E_DIR_PARTS: frozenset[str] = frozenset({
    "e2e", "e2e-tests", "end-to-end", "end_to_end",
})
_INTEGRATION_DIR_PARTS: frozenset[str] = frozenset({
    "integration", "integration-tests", "integration_tests",
})
_UNIT_DIR_PARTS: frozenset[str] = frozenset({
    "unit", "unit-tests", "unit_tests",
})

# Infrastructure import signals for Python tests
_PYTHON_INFRA_SIGNALS: frozenset[str] = frozenset({
    "sqlalchemy", "duckdb", "psycopg", "psycopg2",
    "httpx", "requests", "aiohttp",
    "pytest_httpserver", "responses", "httpretty",
    "testcontainers", "celery", "redis",
})

# e2e signals for TypeScript/JavaScript tests
_TS_E2E_SIGNALS: frozenset[str] = frozenset({
    "playwright", "cypress",
})

# Integration prefix for TypeScript/JavaScript tests
_TS_INTEGRATION_PREFIX = "@testing-library"


def _try_import_typescript_adapter():
    """Import TypeScriptAdapter non-fatally (may not be available)."""
    try:
        from lattice.adapters.typescript_adapter import TypeScriptAdapter  # noqa: PLC0415
        return TypeScriptAdapter
    except Exception:
        return None


def _detect_language(path: Path) -> Literal["python", "javascript", "typescript"]:
    """Infer language from file extension."""
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    return "javascript"


class TestClassifier:
    """Classifies test files by type using directory-then-import heuristics.

    Args:
        project_root: Root directory of the project.
        graph_node_keys: Set of known node keys from the dependency graph.
            Used to distinguish internal imports (have a node) from external
            third-party imports (no matching node).
    """

    def __init__(self, project_root: Path, graph_node_keys: set[str]) -> None:
        self._project_root = project_root
        self._graph_node_keys = graph_node_keys
        self._python_adapter = PythonAdapter(project_root)
        self._ts_adapter_class = _try_import_typescript_adapter()

    def classify(self, test_path: Path) -> TestFile:
        """Classify a single test file.

        Args:
            test_path: Absolute path to the test file.

        Returns:
            TestFile with path, language, test_type, reason, and source_modules.
        """
        language = _detect_language(test_path)
        project_relative = self._to_project_relative(test_path)

        # --- Stage 1: Directory convention ---
        dir_type, dir_reason = self._classify_by_directory(test_path)

        # Always analyze imports to populate source_modules
        source_modules = self._get_source_modules(test_path, language)

        if dir_type is not None:
            return TestFile(
                path=project_relative,
                language=language,
                test_type=dir_type,
                reason=dir_reason,
                source_modules=source_modules,
            )

        # --- Stage 2: Import-based fallback ---
        import_type, import_reason = self._classify_by_imports(test_path, language)
        return TestFile(
            path=project_relative,
            language=language,
            test_type=import_type,
            reason=import_reason,
            source_modules=source_modules,
        )

    def classify_all(self, test_paths: list[Path]) -> list[TestFile]:
        """Classify all test files.

        Args:
            test_paths: List of absolute paths to test files.

        Returns:
            List of TestFile objects, one per input path.
        """
        return [self.classify(p) for p in test_paths]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _to_project_relative(self, path: Path) -> str:
        """Return a project-relative string path."""
        try:
            return str(path.relative_to(self._project_root))
        except ValueError:
            return str(path)

    def _classify_by_directory(
        self, test_path: Path
    ) -> tuple[TestType | None, str]:
        """Check path parts for directory convention markers.

        Returns:
            (test_type, reason) if a convention match is found, else (None, "").
        """
        try:
            relative = test_path.relative_to(self._project_root)
        except ValueError:
            relative = test_path

        parts = set(relative.parts[:-1])  # exclude the filename

        if parts & _E2E_DIR_PARTS:
            matched = next(p for p in relative.parts[:-1] if p in _E2E_DIR_PARTS)
            return "e2e", f"located in {matched}/ directory"

        if parts & _INTEGRATION_DIR_PARTS:
            matched = next(
                p for p in relative.parts[:-1] if p in _INTEGRATION_DIR_PARTS
            )
            return "integration", f"located in {matched}/ directory"

        if parts & _UNIT_DIR_PARTS:
            matched = next(p for p in relative.parts[:-1] if p in _UNIT_DIR_PARTS)
            return "unit", f"located in {matched}/ directory"

        return None, ""

    def _classify_by_imports(
        self, test_path: Path, language: str
    ) -> tuple[TestType, str]:
        """Classify based on external infrastructure signals and internal import count.

        Returns:
            (test_type, reason)
        """
        if language == "python":
            return self._classify_python_by_imports(test_path)
        return self._classify_ts_by_imports(test_path)

    def _classify_python_by_imports(self, test_path: Path) -> tuple[TestType, str]:
        """Classify a Python test file by analyzing its imports."""
        try:
            analysis = self._python_adapter.analyze(test_path)
        except Exception:
            return "unit", "could not analyze imports; defaulting to unit"

        external_modules = {
            imp.module.split(".")[0]
            for imp in analysis.imports
            if imp.is_external
        }

        # Check infrastructure signals — e2e first, then integration
        # (No Python e2e signals in the spec; playwright is TS-only)
        infra_hit = external_modules & _PYTHON_INFRA_SIGNALS
        if infra_hit:
            signal_list = ", ".join(sorted(infra_hit))
            return "integration", f"imports infrastructure library: {signal_list}"

        # Count internal source modules
        source_count = len(self._get_source_modules(test_path, "python"))
        if source_count >= 2:
            return "integration", f"imports {source_count} source modules"
        if source_count == 1:
            return "unit", "imports 1 source module"
        return "unit", "imports no source modules"

    def _classify_ts_by_imports(self, test_path: Path) -> tuple[TestType, str]:
        """Classify a TypeScript/JavaScript test file by analyzing its imports."""
        if self._ts_adapter_class is None:
            # TypeScriptAdapter unavailable — fall back to basic heuristic
            return "unit", "TypeScript adapter unavailable; defaulting to unit"

        try:
            adapter = self._ts_adapter_class(self._project_root)
            analysis = adapter.analyze(test_path)
        except Exception:
            return "unit", "could not analyze TS imports; defaulting to unit"

        for imp in analysis.imports:
            module = imp.module

            # e2e signals take top priority
            top = module.split("/")[0].split("@")[-1] if not module.startswith("@") else module
            if any(signal in module for signal in _TS_E2E_SIGNALS):
                return "e2e", f"imports e2e framework: {module}"

            # integration signals
            if module.startswith(_TS_INTEGRATION_PREFIX):
                return "integration", f"imports {module}"

        # Fall back to source module count
        source_count = len(self._get_source_modules(test_path, "typescript"))
        if source_count >= 2:
            return "integration", f"imports {source_count} source modules"
        if source_count == 1:
            return "unit", "imports 1 source module"
        return "unit", "imports no source modules"

    def _get_source_modules(self, test_path: Path, language: str) -> list[str]:
        """Return project-relative paths of internal source module imports.

        Uses PythonAdapter for Python files or TypeScriptAdapter for TS/JS files.
        Internal = is_external=False AND resolved_path matches a known graph node.
        """
        if language == "python":
            return self._get_python_source_modules(test_path)
        return self._get_ts_source_modules(test_path)

    def _get_python_source_modules(self, test_path: Path) -> list[str]:
        """Extract internal source module paths from a Python test file."""
        try:
            analysis = self._python_adapter.analyze(test_path)
        except Exception:
            return []

        modules: list[str] = []
        for imp in analysis.imports:
            if imp.is_external or imp.resolved_path is None:
                continue
            normalised = DependencyGraphBuilder._normalise_path(
                imp.resolved_path, self._project_root
            )
            if normalised in self._graph_node_keys:
                modules.append(normalised)

        return sorted(set(modules))

    def _get_ts_source_modules(self, test_path: Path) -> list[str]:
        """Extract internal source module paths from a TS/JS test file."""
        if self._ts_adapter_class is None:
            return []

        try:
            adapter = self._ts_adapter_class(self._project_root)
            analysis = adapter.analyze(test_path)
        except Exception:
            return []

        modules: list[str] = []
        for imp in analysis.imports:
            if imp.is_external or imp.resolved_path is None:
                continue
            normalised = DependencyGraphBuilder._normalise_path(
                imp.resolved_path, self._project_root
            )
            if normalised in self._graph_node_keys:
                modules.append(normalised)

        return sorted(set(modules))
