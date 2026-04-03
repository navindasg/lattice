"""Tests for TypeScriptAdapter — subprocess bridge via ts-morph Node.js parser.

All tests require Node.js to be available. Tests are skipped if node is not found.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

# Mark to skip tests if Node.js is not available
NODE_AVAILABLE = shutil.which("node") is not None
skip_no_node = pytest.mark.skipif(
    not NODE_AVAILABLE,
    reason="Node.js is not available on PATH",
)

# Fixture directory for TypeScript test files
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sample_typescript"


@pytest.fixture(scope="module")
def project_root() -> Path:
    """Return the fixtures/sample_typescript directory as project root."""
    return FIXTURES_DIR


@pytest.fixture(scope="module")
def adapter(project_root: Path):
    """Create TypeScriptAdapter instance for tests."""
    from lattice.adapters.typescript_adapter import TypeScriptAdapter
    return TypeScriptAdapter(project_root=project_root)


class TestSupportedExtensions:
    """TypeScriptAdapter.supported_extensions covers TS and JS variants."""

    def test_includes_ts(self, adapter):
        assert ".ts" in adapter.supported_extensions

    def test_includes_tsx(self, adapter):
        assert ".tsx" in adapter.supported_extensions

    def test_includes_js(self, adapter):
        assert ".js" in adapter.supported_extensions

    def test_includes_jsx(self, adapter):
        assert ".jsx" in adapter.supported_extensions

    def test_is_frozenset(self, adapter):
        assert isinstance(adapter.supported_extensions, frozenset)

    def test_exact_set(self, adapter):
        assert adapter.supported_extensions == frozenset({".ts", ".tsx", ".js", ".jsx"})


class TestLanguageField:
    """Language field is set based on file extension."""

    @skip_no_node
    def test_ts_file_language_is_typescript(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "utils.ts")
        assert result.language == "typescript"

    @skip_no_node
    def test_js_file_language_is_javascript(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "legacy.js")
        assert result.language == "javascript"


class TestStaticESModuleImports:
    """Static ES module imports are parsed correctly."""

    @skip_no_node
    def test_routes_ts_has_express_import(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        express_imports = [i for i in result.imports if i.module == "express"]
        assert len(express_imports) == 1

    @skip_no_node
    def test_express_import_is_external(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        express_import = next(i for i in result.imports if i.module == "express")
        assert express_import.is_external is True

    @skip_no_node
    def test_express_named_imports_extracted(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        express_import = next(i for i in result.imports if i.module == "express")
        assert "Router" in express_import.names
        assert "Request" in express_import.names
        assert "Response" in express_import.names

    @skip_no_node
    def test_index_ts_imports_from_routes(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "index.ts")
        route_imports = [i for i in result.imports if i.module == "./routes"]
        assert len(route_imports) == 1

    @skip_no_node
    def test_relative_import_not_external(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        utils_import = next(i for i in result.imports if i.module == "./utils")
        assert utils_import.is_external is False

    @skip_no_node
    def test_relative_import_type(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        utils_import = next(i for i in result.imports if i.module == "./utils")
        assert utils_import.import_type == "relative"

    @skip_no_node
    def test_external_import_type_is_standard(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        express_import = next(i for i in result.imports if i.module == "express")
        assert express_import.import_type == "standard"


class TestCommonJSRequire:
    """CommonJS require() calls are parsed."""

    @skip_no_node
    def test_legacy_js_has_fs_require(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "legacy.js")
        fs_imports = [i for i in result.imports if i.module == "fs"]
        assert len(fs_imports) == 1

    @skip_no_node
    def test_legacy_js_fs_is_external(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "legacy.js")
        fs_import = next(i for i in result.imports if i.module == "fs")
        assert fs_import.is_external is True

    @skip_no_node
    def test_legacy_js_path_require(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "legacy.js")
        path_imports = [i for i in result.imports if i.module == "path"]
        assert len(path_imports) == 1

    @skip_no_node
    def test_legacy_js_relative_require(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "legacy.js")
        utils_imports = [i for i in result.imports if i.module == "./utils"]
        assert len(utils_imports) == 1

    @skip_no_node
    def test_legacy_js_relative_require_not_external(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "legacy.js")
        utils_import = next(i for i in result.imports if i.module == "./utils")
        assert utils_import.is_external is False


class TestDynamicImport:
    """Dynamic import() expressions are flagged with import_type='dynamic'."""

    @skip_no_node
    def test_routes_ts_has_dynamic_import(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        dynamic_imports = [i for i in result.imports if i.import_type == "dynamic"]
        assert len(dynamic_imports) == 1

    @skip_no_node
    def test_dynamic_import_module(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        dynamic_import = next(i for i in result.imports if i.import_type == "dynamic")
        assert dynamic_import.module == "./legacy"

    @skip_no_node
    def test_dynamic_import_has_raw_expression(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        dynamic_import = next(i for i in result.imports if i.import_type == "dynamic")
        assert dynamic_import.raw_expression is not None
        assert "import(" in dynamic_import.raw_expression

    @skip_no_node
    def test_dynamic_import_not_in_static_imports(self, adapter):
        """Dynamic imports must NOT be double-counted in static import list."""
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        static_legacy = [
            i for i in result.imports
            if i.module == "./legacy" and i.import_type != "dynamic"
        ]
        assert len(static_legacy) == 0


class TestBarrelExports:
    """Barrel exports (export * from) are detected in exports array."""

    @skip_no_node
    def test_index_ts_has_barrel_export(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "index.ts")
        barrel_exports = [e for e in result.exports if "* from" in e]
        assert len(barrel_exports) >= 1

    @skip_no_node
    def test_barrel_export_references_routes(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "index.ts")
        barrel_exports = [e for e in result.exports if "* from" in e and "routes" in e]
        assert len(barrel_exports) == 1

    @skip_no_node
    def test_index_ts_has_named_reexports(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "index.ts")
        # export { helper, formatDate } from './utils'
        assert any("helper" in e for e in result.exports)

    @skip_no_node
    def test_utils_ts_exports(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "utils.ts")
        assert "helper" in result.exports or any("helper" in e for e in result.exports)


class TestFileAnalysisStructure:
    """Returned FileAnalysis matches the expected contract."""

    @skip_no_node
    def test_analyze_returns_file_analysis(self, adapter):
        from lattice.models.analysis import FileAnalysis
        result = adapter.analyze(FIXTURES_DIR / "utils.ts")
        assert isinstance(result, FileAnalysis)

    @skip_no_node
    def test_path_field_is_string(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "utils.ts")
        assert isinstance(result.path, str)

    @skip_no_node
    def test_imports_are_import_info_instances(self, adapter):
        from lattice.models.analysis import ImportInfo
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        for imp in result.imports:
            assert isinstance(imp, ImportInfo)

    @skip_no_node
    def test_line_numbers_are_positive(self, adapter):
        result = adapter.analyze(FIXTURES_DIR / "routes.ts")
        for imp in result.imports:
            assert imp.line_number > 0


class TestErrorHandling:
    """Adapter raises RuntimeError on failures."""

    @skip_no_node
    def test_nonexistent_file_raises(self, project_root: Path):
        from lattice.adapters.typescript_adapter import TypeScriptAdapter
        adapter = TypeScriptAdapter(project_root=project_root)
        with pytest.raises(RuntimeError, match="nonexistent"):
            adapter.analyze(FIXTURES_DIR / "nonexistent_file.ts")

    def test_node_not_found_raises_runtime_error(self, tmp_path: Path, monkeypatch):
        """If node is not on PATH, __init__ raises RuntimeError with clear message."""
        # Temporarily remove node from PATH
        monkeypatch.setenv("PATH", str(tmp_path))
        from lattice.adapters import typescript_adapter
        import importlib
        # Force reimport to re-evaluate node check
        importlib.reload(typescript_adapter)
        with pytest.raises(RuntimeError, match="Node.js"):
            typescript_adapter.TypeScriptAdapter(project_root=tmp_path)
        # Restore by reimporting (monkeypatch restores env after test)
        importlib.reload(typescript_adapter)
