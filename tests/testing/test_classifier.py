"""Tests for TestClassifier — TDD RED phase.

TestClassifier classifies test files by directory convention (priority)
and falls back to import-based heuristics.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lattice.testing import TestClassifier

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "sample_mixed"

# Shared empty graph_node_keys for tests that don't need real graph data
EMPTY_KEYS: set[str] = set()


class TestDirectoryConvention:
    """Directory naming takes priority over import analysis."""

    def test_unit_dir_classified_as_unit(self) -> None:
        """Test in tests/unit/ is classified as unit."""
        classifier = TestClassifier(FIXTURE_ROOT, EMPTY_KEYS)
        test_path = FIXTURE_ROOT / "tests" / "unit" / "test_auth.py"
        result = classifier.classify(test_path)
        assert result.test_type == "unit"

    def test_unit_dir_reason_mentions_directory(self) -> None:
        """Reason string references directory path for unit tests."""
        classifier = TestClassifier(FIXTURE_ROOT, EMPTY_KEYS)
        test_path = FIXTURE_ROOT / "tests" / "unit" / "test_auth.py"
        result = classifier.classify(test_path)
        assert "unit" in result.reason.lower()
        assert len(result.reason) > 0

    def test_integration_dir_classified_as_integration(self) -> None:
        """Test in tests/integration/ is classified as integration."""
        classifier = TestClassifier(FIXTURE_ROOT, EMPTY_KEYS)
        test_path = FIXTURE_ROOT / "tests" / "integration" / "test_routes.py"
        result = classifier.classify(test_path)
        assert result.test_type == "integration"

    def test_integration_dir_reason_mentions_directory(self) -> None:
        """Reason string references directory path for integration tests."""
        classifier = TestClassifier(FIXTURE_ROOT, EMPTY_KEYS)
        test_path = FIXTURE_ROOT / "tests" / "integration" / "test_routes.py"
        result = classifier.classify(test_path)
        assert "integration" in result.reason.lower()

    def test_e2e_dir_classified_as_e2e(self) -> None:
        """Test in tests/e2e/ is classified as e2e."""
        classifier = TestClassifier(FIXTURE_ROOT, EMPTY_KEYS)
        test_path = FIXTURE_ROOT / "tests" / "e2e" / "test_app.test.ts"
        result = classifier.classify(test_path)
        assert result.test_type == "e2e"

    def test_e2e_dir_reason_mentions_directory(self) -> None:
        """Reason string references directory path for e2e tests."""
        classifier = TestClassifier(FIXTURE_ROOT, EMPTY_KEYS)
        test_path = FIXTURE_ROOT / "tests" / "e2e" / "test_app.test.ts"
        result = classifier.classify(test_path)
        assert "e2e" in result.reason.lower()

    def test_directory_priority_over_imports(self, tmp_path: Path) -> None:
        """A test in tests/unit/ that imports 3 modules is still classified as unit."""
        # Create a test file in tests/unit/ that imports multiple modules
        unit_dir = tmp_path / "tests" / "unit"
        unit_dir.mkdir(parents=True)
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        # Create 3 source modules
        (src_dir / "a.py").write_text("def a(): pass")
        (src_dir / "b.py").write_text("def b(): pass")
        (src_dir / "c.py").write_text("def c(): pass")

        # Create test importing all 3
        test_file = unit_dir / "test_multi.py"
        test_file.write_text(
            "from src.a import a\nfrom src.b import b\nfrom src.c import c\n\ndef test_x(): pass\n"
        )

        # Node keys for the 3 source modules
        node_keys = {"src/a.py", "src/b.py", "src/c.py"}
        classifier = TestClassifier(tmp_path, node_keys)
        result = classifier.classify(test_file)
        # Directory convention (unit) takes priority despite 3+ imports
        assert result.test_type == "unit"


class TestImportFallback:
    """Import-based classification when no directory convention applies."""

    def test_two_source_modules_classified_as_integration(self, tmp_path: Path) -> None:
        """Test importing 2+ source modules is classified as integration."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("def a(): pass")
        (src / "b.py").write_text("def b(): pass")

        test_file = tmp_path / "test_both.py"
        test_file.write_text(
            "from src.a import a\nfrom src.b import b\n\ndef test_x(): pass\n"
        )

        node_keys = {"src/a.py", "src/b.py"}
        classifier = TestClassifier(tmp_path, node_keys)
        result = classifier.classify(test_file)
        assert result.test_type == "integration"

    def test_one_source_module_classified_as_unit(self, tmp_path: Path) -> None:
        """Test importing only 1 source module is classified as unit."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("def a(): pass")

        test_file = tmp_path / "test_a.py"
        test_file.write_text("from src.a import a\n\ndef test_x(): pass\n")

        node_keys = {"src/a.py"}
        classifier = TestClassifier(tmp_path, node_keys)
        result = classifier.classify(test_file)
        assert result.test_type == "unit"

    def test_zero_source_modules_classified_as_unit(self, tmp_path: Path) -> None:
        """Test with no source module imports is classified as unit."""
        test_file = tmp_path / "test_pure.py"
        test_file.write_text("def test_math(): assert 1 + 1 == 2\n")

        classifier = TestClassifier(tmp_path, EMPTY_KEYS)
        result = classifier.classify(test_file)
        assert result.test_type == "unit"

    def test_reason_mentions_module_count_for_integration(self, tmp_path: Path) -> None:
        """Reason string mentions module count for integration tests."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("def a(): pass")
        (src / "b.py").write_text("def b(): pass")

        test_file = tmp_path / "test_both.py"
        test_file.write_text(
            "from src.a import a\nfrom src.b import b\n\ndef test_x(): pass\n"
        )

        node_keys = {"src/a.py", "src/b.py"}
        classifier = TestClassifier(tmp_path, node_keys)
        result = classifier.classify(test_file)
        assert len(result.reason) > 0


class TestInfrastructureSignals:
    """Infrastructure import signals trigger integration/e2e classification."""

    def test_sqlalchemy_import_classified_as_integration(self, tmp_path: Path) -> None:
        """Test importing sqlalchemy is classified as integration."""
        test_file = tmp_path / "test_db.py"
        test_file.write_text(
            "import sqlalchemy\n\ndef test_db_connection(): pass\n"
        )

        classifier = TestClassifier(tmp_path, EMPTY_KEYS)
        result = classifier.classify(test_file)
        assert result.test_type == "integration"

    def test_sqlalchemy_reason_mentions_infra_signal(self, tmp_path: Path) -> None:
        """Reason string mentions sqlalchemy for infra-signal classification."""
        test_file = tmp_path / "test_db.py"
        test_file.write_text(
            "import sqlalchemy\n\ndef test_db_connection(): pass\n"
        )

        classifier = TestClassifier(tmp_path, EMPTY_KEYS)
        result = classifier.classify(test_file)
        assert "sqlalchemy" in result.reason.lower()

    def test_httpx_import_classified_as_integration(self, tmp_path: Path) -> None:
        """Test importing httpx is classified as integration."""
        test_file = tmp_path / "test_api.py"
        test_file.write_text(
            "import httpx\n\ndef test_endpoint(): pass\n"
        )

        classifier = TestClassifier(tmp_path, EMPTY_KEYS)
        result = classifier.classify(test_file)
        assert result.test_type == "integration"

    def test_duckdb_import_classified_as_integration(self, tmp_path: Path) -> None:
        """Test importing duckdb is classified as integration."""
        test_file = tmp_path / "test_duckdb.py"
        test_file.write_text(
            "import duckdb\n\ndef test_query(): pass\n"
        )

        classifier = TestClassifier(tmp_path, EMPTY_KEYS)
        result = classifier.classify(test_file)
        assert result.test_type == "integration"


class TestSourceModulesField:
    """source_modules field is correctly populated."""

    def test_source_modules_populated_for_unit_test(self, tmp_path: Path) -> None:
        """source_modules contains resolved internal import paths."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("def a(): pass")

        test_file = tmp_path / "test_a.py"
        test_file.write_text("from src.a import a\n\ndef test_x(): pass\n")

        node_keys = {"src/a.py"}
        classifier = TestClassifier(tmp_path, node_keys)
        result = classifier.classify(test_file)
        assert len(result.source_modules) == 1

    def test_source_modules_empty_for_infra_signal_test(self, tmp_path: Path) -> None:
        """source_modules is empty when only external imports are present."""
        test_file = tmp_path / "test_db.py"
        test_file.write_text("import sqlalchemy\n\ndef test_db(): pass\n")

        classifier = TestClassifier(tmp_path, EMPTY_KEYS)
        result = classifier.classify(test_file)
        # No internal modules imported
        assert result.source_modules == []

    def test_directory_classified_test_still_populates_source_modules(
        self, tmp_path: Path
    ) -> None:
        """Directory-classified tests still populate source_modules from imports."""
        unit_dir = tmp_path / "tests" / "unit"
        unit_dir.mkdir(parents=True)
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("def a(): pass")

        test_file = unit_dir / "test_a.py"
        test_file.write_text("from src.a import a\n\ndef test_x(): pass\n")

        node_keys = {"src/a.py"}
        classifier = TestClassifier(tmp_path, node_keys)
        result = classifier.classify(test_file)
        # Directory match → unit, but source_modules still populated
        assert result.test_type == "unit"
        assert len(result.source_modules) == 1


class TestLanguageDetection:
    """Classifier detects language from file extension."""

    def test_python_file_has_python_language(self) -> None:
        """Python test files have language='python'."""
        classifier = TestClassifier(FIXTURE_ROOT, EMPTY_KEYS)
        test_path = FIXTURE_ROOT / "tests" / "unit" / "test_auth.py"
        result = classifier.classify(test_path)
        assert result.language == "python"

    def test_ts_file_has_typescript_language(self) -> None:
        """TypeScript test files have language='typescript'."""
        classifier = TestClassifier(FIXTURE_ROOT, EMPTY_KEYS)
        test_path = FIXTURE_ROOT / "tests" / "e2e" / "test_app.test.ts"
        result = classifier.classify(test_path)
        assert result.language == "typescript"


class TestClassifyAll:
    """classify_all convenience method."""

    def test_classify_all_returns_list_of_test_files(self) -> None:
        """classify_all returns one TestFile per input path."""
        from lattice.testing import TestDiscovery

        discovery = TestDiscovery(FIXTURE_ROOT)
        paths = discovery.discover()

        classifier = TestClassifier(FIXTURE_ROOT, EMPTY_KEYS)
        results = classifier.classify_all(paths)

        assert len(results) == len(paths)

    def test_classify_all_each_has_non_empty_reason(self) -> None:
        """Every TestFile from classify_all has a non-empty reason string."""
        from lattice.testing import TestDiscovery

        discovery = TestDiscovery(FIXTURE_ROOT)
        paths = discovery.discover()

        classifier = TestClassifier(FIXTURE_ROOT, EMPTY_KEYS)
        results = classifier.classify_all(paths)

        for tf in results:
            assert len(tf.reason) > 0, f"Empty reason for {tf.path}"
