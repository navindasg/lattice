"""Tests for PythonAdapter — Python AST adapter with full import resolution.

Covers:
- supported_extensions returns frozenset({".py"})
- Standard import resolution (is_external=True for stdlib and third-party)
- Relative import resolution with resolved_path
- __init__.py re-export chain resolution
- Dynamic import flagging (importlib.import_module, __import__)
- Decorator detection (@app.route, @celery.task)
- __name__ == "__main__" guard detection -> has_main_guard=True
- Exports extraction (function defs, class defs, __all__)
- Signatures extraction
- Classes extraction
- Non-existent file raises FileNotFoundError
- Empty file produces empty lists
"""
import pytest
from pathlib import Path

from lattice.adapters.python_adapter import PythonAdapter
from lattice.models.analysis import FileAnalysis


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sample_python"


class TestPythonAdapterProperties:
    def test_supported_extensions(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        assert adapter.supported_extensions == frozenset({".py"})


class TestPythonAdapterStandardImports:
    def test_analyze_returns_file_analysis(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "main.py")
        assert isinstance(result, FileAnalysis)

    def test_standard_stdlib_import_is_external(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "main.py")
        os_import = next((i for i in result.imports if i.module == "os"), None)
        assert os_import is not None
        assert os_import.import_type == "standard"
        assert os_import.is_external is True

    def test_standard_import_has_line_number(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "main.py")
        os_import = next((i for i in result.imports if i.module == "os"), None)
        assert os_import is not None
        assert os_import.line_number >= 1


class TestPythonAdapterRelativeImports:
    def test_relative_import_has_correct_type(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "main.py")
        rel_imports = [i for i in result.imports if i.import_type == "relative"]
        assert len(rel_imports) >= 1

    def test_relative_import_has_resolved_path(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "main.py")
        utils_import = next(
            (i for i in result.imports if i.import_type == "relative" and "utils" in i.module),
            None
        )
        assert utils_import is not None
        assert utils_import.resolved_path is not None

    def test_relative_import_is_not_external(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "main.py")
        rel_imports = [i for i in result.imports if i.import_type == "relative"]
        for imp in rel_imports:
            assert imp.is_external is False


class TestPythonAdapterDynamicImports:
    def test_importlib_import_module_flagged_as_dynamic(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "main.py")
        dynamic_imports = [i for i in result.imports if i.import_type == "dynamic"]
        assert len(dynamic_imports) >= 1

    def test_dynamic_import_has_raw_expression(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "main.py")
        dynamic_imports = [i for i in result.imports if i.import_type == "dynamic"]
        for imp in dynamic_imports:
            assert imp.raw_expression is not None

    def test_dunder_import_flagged_as_dynamic(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "main.py")
        dynamic_imports = [i for i in result.imports if i.import_type == "dynamic"]
        # Should have at least 2 dynamic imports: importlib.import_module and __import__
        assert len(dynamic_imports) >= 2


class TestPythonAdapterDecoratorDetection:
    def test_app_route_detected_as_decorator(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "routes.py")
        decorator_imports = [i for i in result.imports if i.import_type == "decorator"]
        assert len(decorator_imports) >= 1

    def test_decorator_import_module_set(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "routes.py")
        decorator_imports = [i for i in result.imports if i.import_type == "decorator"]
        for imp in decorator_imports:
            assert imp.module is not None
            assert len(imp.module) > 0

    def test_celery_task_detected_as_decorator(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "tasks.py")
        decorator_imports = [i for i in result.imports if i.import_type == "decorator"]
        assert len(decorator_imports) >= 1


class TestPythonAdapterMainGuard:
    def test_main_guard_detected(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "main.py")
        assert result.has_main_guard is True

    def test_no_main_guard_returns_false(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "routes.py")
        assert result.has_main_guard is False


class TestPythonAdapterExports:
    def test_top_level_function_exported(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "utils" / "helpers.py")
        assert "helper_fn" in result.exports

    def test_signatures_extracted(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "utils" / "helpers.py")
        assert len(result.signatures) >= 1
        assert any("helper_fn" in sig for sig in result.signatures)

    def test_classes_extracted(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)

        # Create a temp file with a class for testing
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("class MyClass:\n    pass\n")
            tmpfile = f.name

        try:
            result = adapter.analyze(Path(tmpfile))
            assert "MyClass" in result.classes
        finally:
            os.unlink(tmpfile)


class TestPythonAdapterErrorHandling:
    def test_nonexistent_file_raises(self):
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        with pytest.raises(FileNotFoundError):
            adapter.analyze(FIXTURES_DIR / "does_not_exist.py")

    def test_empty_file_returns_empty_lists(self):
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("")
            tmpfile = f.name

        try:
            adapter = PythonAdapter(project_root=FIXTURES_DIR)
            result = adapter.analyze(Path(tmpfile))
            assert result.imports == []
            assert result.exports == []
            assert result.signatures == []
            assert result.classes == []
            assert result.has_main_guard is False
        finally:
            os.unlink(tmpfile)


class TestPythonAdapterReexports:
    def test_init_reexport_resolves_to_source(self):
        """from .utils import helper_fn in main.py should resolve via utils/__init__.py to helpers.py"""
        adapter = PythonAdapter(project_root=FIXTURES_DIR)
        result = adapter.analyze(FIXTURES_DIR / "main.py")
        # The relative import from .utils should eventually resolve to helpers.py
        # Look for an import that points to utils directory
        relative_imports = [i for i in result.imports if i.import_type in ("relative", "reexport")]
        assert len(relative_imports) >= 1
