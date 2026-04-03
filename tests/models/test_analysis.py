"""Tests for FileAnalysis, GraphNode, and ImportInfo Pydantic v2 models.

Covers:
- Required field validation (ValidationError on missing fields)
- Type coercion and type errors (ValidationError on wrong types)
- Frozen model enforcement (TypeError on mutation)
- Default factory fields (empty lists, utcnow datetime)
- GraphNode language validator (supported languages only)
- ImportInfo field validation and Literal constraint on import_type
- FileAnalysis.imports as list[ImportInfo]
- FileAnalysis.has_main_guard field
"""
import pytest
from pydantic import ValidationError

from lattice.models.analysis import FileAnalysis, GraphNode, ImportInfo


# ---------------------------------------------------------------------------
# ImportInfo
# ---------------------------------------------------------------------------

class TestImportInfoValid:
    def test_standard_import(self):
        info = ImportInfo(
            module="os",
            import_type="standard",
            line_number=1,
        )
        assert info.module == "os"
        assert info.import_type == "standard"
        assert info.line_number == 1
        assert info.names == []
        assert info.is_external is False
        assert info.resolved_path is None
        assert info.raw_expression is None

    def test_relative_import_with_resolved_path(self):
        info = ImportInfo(
            module="utils.helpers",
            import_type="relative",
            resolved_path="src/utils/helpers.py",
            names=["helper_fn"],
            line_number=5,
        )
        assert info.import_type == "relative"
        assert info.resolved_path == "src/utils/helpers.py"
        assert info.names == ["helper_fn"]

    def test_reexport_import(self):
        info = ImportInfo(
            module="pkg.sub",
            import_type="reexport",
            resolved_path="src/pkg/sub.py",
            line_number=2,
        )
        assert info.import_type == "reexport"

    def test_dynamic_import_with_raw_expression(self):
        info = ImportInfo(
            module="dynamic_mod",
            import_type="dynamic",
            raw_expression='importlib.import_module("dynamic_mod")',
            line_number=10,
        )
        assert info.import_type == "dynamic"
        assert info.raw_expression == 'importlib.import_module("dynamic_mod")'

    def test_decorator_import(self):
        info = ImportInfo(
            module="flask",
            import_type="decorator",
            line_number=3,
        )
        assert info.import_type == "decorator"

    def test_config_ref_import(self):
        info = ImportInfo(
            module="config",
            import_type="config_ref",
            line_number=7,
        )
        assert info.import_type == "config_ref"

    def test_external_flag(self):
        info = ImportInfo(
            module="requests",
            import_type="standard",
            is_external=True,
            line_number=1,
        )
        assert info.is_external is True

    def test_all_valid_import_types(self):
        valid_types = ["standard", "relative", "reexport", "dynamic", "decorator", "config_ref"]
        for t in valid_types:
            info = ImportInfo(module="x", import_type=t, line_number=1)
            assert info.import_type == t


class TestImportInfoInvalid:
    def test_invalid_import_type_raises(self):
        with pytest.raises(ValidationError):
            ImportInfo(module="os", import_type="unknown", line_number=1)

    def test_missing_module_raises(self):
        with pytest.raises(ValidationError):
            ImportInfo(import_type="standard", line_number=1)

    def test_missing_line_number_raises(self):
        with pytest.raises(ValidationError):
            ImportInfo(module="os", import_type="standard")

    def test_missing_import_type_raises(self):
        with pytest.raises(ValidationError):
            ImportInfo(module="os", line_number=1)


class TestImportInfoFrozen:
    def test_frozen_raises_on_mutation(self):
        info = ImportInfo(module="os", import_type="standard", line_number=1)
        with pytest.raises(Exception):
            info.module = "sys"

    def test_is_frozen_config(self):
        assert ImportInfo.model_config.get("frozen") is True


# ---------------------------------------------------------------------------
# FileAnalysis with ImportInfo
# ---------------------------------------------------------------------------

class TestFileAnalysisValid:
    def test_creates_with_required_fields(self):
        fa = FileAnalysis(path="src/main.py", language="python")
        assert fa.path == "src/main.py"
        assert fa.language == "python"

    def test_default_list_fields_are_empty(self):
        fa = FileAnalysis(path="src/main.py", language="python")
        assert fa.imports == []
        assert fa.exports == []
        assert fa.signatures == []
        assert fa.classes == []

    def test_default_analyzed_at_is_set(self):
        from datetime import datetime

        fa = FileAnalysis(path="src/main.py", language="python")
        assert isinstance(fa.analyzed_at, datetime)

    def test_default_has_main_guard_is_false(self):
        fa = FileAnalysis(path="src/main.py", language="python")
        assert fa.has_main_guard is False

    def test_has_main_guard_can_be_set_true(self):
        fa = FileAnalysis(path="src/main.py", language="python", has_main_guard=True)
        assert fa.has_main_guard is True

    def test_accepts_import_info_list(self):
        import_info = ImportInfo(module="os", import_type="standard", is_external=True, line_number=1)
        fa = FileAnalysis(
            path="src/main.py",
            language="python",
            imports=[import_info],
            exports=["MyClass"],
            signatures=["def foo(x: int) -> str"],
            classes=["MyClass"],
        )
        assert len(fa.imports) == 1
        assert fa.imports[0].module == "os"
        assert fa.exports == ["MyClass"]

    def test_empty_imports_list_is_valid(self):
        fa = FileAnalysis(path="src/main.py", language="python", imports=[])
        assert fa.imports == []

    def test_multiple_import_infos(self):
        imports = [
            ImportInfo(module="os", import_type="standard", is_external=True, line_number=1),
            ImportInfo(module="sys", import_type="standard", is_external=True, line_number=2),
            ImportInfo(module=".utils", import_type="relative", resolved_path="src/utils.py", line_number=3),
        ]
        fa = FileAnalysis(path="src/main.py", language="python", imports=imports)
        assert len(fa.imports) == 3


class TestFileAnalysisInvalid:
    def test_missing_path_raises(self):
        with pytest.raises(ValidationError):
            FileAnalysis(language="python")

    def test_missing_language_raises(self):
        with pytest.raises(ValidationError):
            FileAnalysis(path="src/main.py")

    def test_missing_both_required_raises(self):
        with pytest.raises(ValidationError):
            FileAnalysis()

    def test_wrong_type_for_path_raises(self):
        with pytest.raises(ValidationError):
            FileAnalysis(path=123, language="python")

    def test_wrong_type_for_imports_raises(self):
        with pytest.raises(ValidationError):
            FileAnalysis(path="src/main.py", language="python", imports="not-a-list")

    def test_imports_with_strings_rejected(self):
        """FileAnalysis.imports must contain ImportInfo objects, not strings."""
        with pytest.raises(ValidationError):
            FileAnalysis(
                path="src/main.py",
                language="python",
                imports=["os", "sys"],  # strings not accepted
            )


class TestFileAnalysisFrozen:
    def test_assignment_raises_error(self):
        fa = FileAnalysis(path="src/main.py", language="python")
        with pytest.raises(Exception):  # ValidationError or TypeError depending on pydantic version
            fa.path = "other.py"

    def test_is_frozen_config(self):
        assert FileAnalysis.model_config.get("frozen") is True


# ---------------------------------------------------------------------------
# GraphNode
# ---------------------------------------------------------------------------

class TestGraphNodeValid:
    def test_creates_with_python(self):
        node = GraphNode(id="node-1", path="src/main.py", language="python")
        assert node.language == "python"

    def test_creates_with_typescript(self):
        node = GraphNode(id="node-1", path="src/app.ts", language="typescript")
        assert node.language == "typescript"

    def test_creates_with_javascript(self):
        node = GraphNode(id="node-1", path="src/app.js", language="javascript")
        assert node.language == "javascript"

    def test_default_edges_empty(self):
        node = GraphNode(id="node-1", path="src/main.py", language="python")
        assert node.edges == []


class TestGraphNodeInvalid:
    def test_unsupported_language_raises(self):
        with pytest.raises(ValidationError):
            GraphNode(id="node-1", path="src/main.rb", language="ruby")

    def test_empty_language_raises(self):
        with pytest.raises(ValidationError):
            GraphNode(id="node-1", path="src/main.py", language="")

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            GraphNode()


class TestGraphNodeFrozen:
    def test_assignment_raises_error(self):
        node = GraphNode(id="node-1", path="src/main.py", language="python")
        with pytest.raises(Exception):
            node.path = "other.py"

    def test_is_frozen_config(self):
        assert GraphNode.model_config.get("frozen") is True
