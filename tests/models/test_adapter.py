"""Tests for LanguageAdapter ABC and lattice package public exports.

Covers:
- LanguageAdapter cannot be instantiated directly (TypeError)
- Subclass missing analyze() raises TypeError
- Subclass missing supported_extensions raises TypeError
- Concrete subclass with both methods instantiates successfully
- analyze() return type annotation is FileAnalysis
- supported_extensions returns frozenset[str]
- All shared types importable from lattice root
"""
import inspect
from pathlib import Path

import pytest

from lattice.adapters.base import LanguageAdapter
from lattice.models.analysis import FileAnalysis


# ---------------------------------------------------------------------------
# Concrete mock adapter for testing valid subclass
# ---------------------------------------------------------------------------

class MockAdapter(LanguageAdapter):
    """A concrete adapter used only in tests."""

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".py"})

    def analyze(self, path: Path) -> FileAnalysis:
        return FileAnalysis(
            path=str(path),
            language="python",
        )


# ---------------------------------------------------------------------------
# ABC enforcement
# ---------------------------------------------------------------------------

class TestLanguageAdapterABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            LanguageAdapter()

    def test_subclass_missing_analyze_raises(self):
        with pytest.raises(TypeError):
            class NoAnalyze(LanguageAdapter):
                @property
                def supported_extensions(self) -> frozenset[str]:
                    return frozenset()

            NoAnalyze()

    def test_subclass_missing_supported_extensions_raises(self):
        with pytest.raises(TypeError):
            class NoExtensions(LanguageAdapter):
                def analyze(self, path: Path) -> FileAnalysis:
                    return FileAnalysis(path=str(path), language="python")

            NoExtensions()

    def test_concrete_subclass_instantiates_successfully(self):
        adapter = MockAdapter()
        assert adapter is not None


# ---------------------------------------------------------------------------
# Method signatures
# ---------------------------------------------------------------------------

class TestLanguageAdapterInterface:
    def test_supported_extensions_returns_frozenset(self):
        adapter = MockAdapter()
        result = adapter.supported_extensions
        assert isinstance(result, frozenset)
        assert ".py" in result

    def test_analyze_returns_file_analysis(self):
        adapter = MockAdapter()
        result = adapter.analyze(Path("src/main.py"))
        assert isinstance(result, FileAnalysis)
        assert result.path == "src/main.py"
        assert result.language == "python"

    def test_analyze_return_annotation_is_file_analysis(self):
        hints = LanguageAdapter.analyze.__annotations__
        assert hints.get("return") is FileAnalysis

    def test_analyze_is_abstract(self):
        assert getattr(LanguageAdapter.analyze, "__isabstractmethod__", False)

    def test_supported_extensions_is_abstract_property(self):
        # supported_extensions must be abstract
        assert getattr(
            LanguageAdapter.supported_extensions.fget, "__isabstractmethod__", False
        )


# ---------------------------------------------------------------------------
# Public exports from lattice root
# ---------------------------------------------------------------------------

class TestLatticePublicExports:
    def test_import_file_analysis_from_root(self):
        from lattice import FileAnalysis as FA
        assert FA is FileAnalysis

    def test_import_graph_node_from_root(self):
        from lattice import GraphNode
        assert GraphNode is not None

    def test_import_mapping_session_from_root(self):
        from lattice import MappingSession
        assert MappingSession is not None

    def test_import_managed_instance_from_root(self):
        from lattice import ManagedInstance
        assert ManagedInstance is not None

    def test_import_mapper_command_from_root(self):
        from lattice import MapperCommand
        assert MapperCommand is not None

    def test_import_language_adapter_from_root(self):
        from lattice import LanguageAdapter as LA
        assert LA is LanguageAdapter

    def test_import_configure_logging_from_root(self):
        from lattice import configure_logging
        assert callable(configure_logging)

    def test_all_exports_in_dunder_all(self):
        import lattice

        expected = {
            "FileAnalysis",
            "GraphNode",
            "MappingSession",
            "ManagedInstance",
            "MapperCommand",
            "LanguageAdapter",
            "configure_logging",
        }
        assert expected.issubset(set(lattice.__all__))
