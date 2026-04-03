"""Tests for TestDiscovery — TDD RED phase.

Discovery finds pytest and jest test files, excludes conftest.py, and skips
excluded directories.
"""
from pathlib import Path

import pytest

from lattice.testing import TestDiscovery

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "sample_mixed"


class TestPythonDiscovery:
    """TestDiscovery finds Python test files using pytest conventions."""

    def test_discovers_test_star_py_pattern(self) -> None:
        """Files matching test_*.py are discovered."""
        discovery = TestDiscovery(FIXTURE_ROOT)
        discovered = discovery.discover()
        names = [p.name for p in discovered]
        assert "test_auth.py" in names

    def test_discovers_test_routes_in_integration_dir(self) -> None:
        """test_*.py files in subdirectories are discovered."""
        discovery = TestDiscovery(FIXTURE_ROOT)
        discovered = discovery.discover()
        names = [p.name for p in discovered]
        assert "test_routes.py" in names

    def test_excludes_conftest_py(self) -> None:
        """conftest.py is NOT included in discovered test files."""
        discovery = TestDiscovery(FIXTURE_ROOT)
        discovered = discovery.discover()
        names = [p.name for p in discovered]
        assert "conftest.py" not in names

    def test_excludes_regular_python_modules(self) -> None:
        """Non-test source files are not discovered."""
        discovery = TestDiscovery(FIXTURE_ROOT)
        discovered = discovery.discover()
        names = [p.name for p in discovered]
        assert "auth.py" not in names
        assert "db.py" not in names
        assert "routes.py" not in names


class TestTypeScriptDiscovery:
    """TestDiscovery finds TypeScript/JavaScript test files using jest conventions."""

    def test_discovers_test_ts_extension(self) -> None:
        """Files matching *.test.ts are discovered."""
        discovery = TestDiscovery(FIXTURE_ROOT)
        discovered = discovery.discover()
        names = [p.name for p in discovered]
        assert "test_app.test.ts" in names

    def test_discovers_tests_in_dunder_tests_dir(self) -> None:
        """TypeScript files in __tests__/ directories are discovered."""
        discovery = TestDiscovery(FIXTURE_ROOT)
        discovered = discovery.discover()
        names = [p.name for p in discovered]
        assert "api.test.ts" in names


class TestSkipDirBehavior:
    """TestDiscovery skips excluded directories."""

    def test_skips_pycache(self, tmp_path: Path) -> None:
        """__pycache__ directories are skipped."""
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "test_cached.py").write_text("def test_foo(): pass")
        discovery = TestDiscovery(tmp_path)
        discovered = discovery.discover()
        assert len(discovered) == 0

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        """node_modules directories are skipped."""
        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        (node_modules / "test.test.ts").write_text("test('x', () => {})")
        discovery = TestDiscovery(tmp_path)
        discovered = discovery.discover()
        assert len(discovered) == 0

    def test_skips_venv(self, tmp_path: Path) -> None:
        """.venv directories are skipped."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "test_something.py").write_text("def test_something(): pass")
        discovery = TestDiscovery(tmp_path)
        discovered = discovery.discover()
        assert len(discovered) == 0


class TestEdgeCases:
    """Edge cases for TestDiscovery."""

    def test_empty_directory_returns_empty_list(self, tmp_path: Path) -> None:
        """Directory with no test files returns empty list."""
        discovery = TestDiscovery(tmp_path)
        assert discovery.discover() == []

    def test_returns_sorted_paths(self) -> None:
        """Discovered paths are sorted."""
        discovery = TestDiscovery(FIXTURE_ROOT)
        discovered = discovery.discover()
        assert discovered == sorted(discovered)

    def test_returns_path_objects(self) -> None:
        """Return value contains Path objects."""
        discovery = TestDiscovery(FIXTURE_ROOT)
        discovered = discovery.discover()
        assert all(isinstance(p, Path) for p in discovered)

    def test_fixture_discovers_expected_count(self) -> None:
        """sample_mixed fixture has exactly 4 test files (2 Python + 2 TS)."""
        discovery = TestDiscovery(FIXTURE_ROOT)
        discovered = discovery.discover()
        assert len(discovered) == 4
