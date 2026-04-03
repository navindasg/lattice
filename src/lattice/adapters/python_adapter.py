"""PythonAdapter — Python AST adapter for codebase intelligence (SA-02).

Parses Python source files using the AST module to extract structured
import information, exports, function signatures, and class names.

Import resolution strategy:
- Standard/stdlib/third-party: classified via sys.stdlib_module_names,
  marked is_external=True
- Relative: resolved via pathlib relative to project_root
- __init__.py re-exports: inspected to resolve to actual source files
- Dynamic: importlib.import_module() and __import__() calls
- Decorator: @app.route, @celery.task, @click.command patterns
- Do NOT use importlib.util.find_spec — path-based resolution only

Usage:
    adapter = PythonAdapter(project_root=Path("/path/to/project"))
    result = adapter.analyze(Path("/path/to/project/src/main.py"))
"""
import ast
import sys
from pathlib import Path
from typing import Any

from lattice.adapters.base import LanguageAdapter
from lattice.models.analysis import FileAnalysis, ImportInfo


# Decorator name patterns that signal registration (not just decoration)
_REGISTRATION_DECORATORS = frozenset({
    "route", "task", "command", "group", "cli",
    "get", "post", "put", "delete", "patch",
})


class _ImportVisitor(ast.NodeVisitor):
    """Collects import statements from a Python AST.

    Handles:
    - import X / import X as Y  -> standard
    - from X import Y            -> standard or relative (level > 0)
    - importlib.import_module()  -> dynamic
    - __import__()               -> dynamic
    """

    def __init__(self, file_path: Path, project_root: Path | None) -> None:
        self.file_path = file_path
        self.project_root = project_root
        self.imports: list[ImportInfo] = []
        self._stdlib = sys.stdlib_module_names

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module = alias.name
            is_external = self._is_external(module)
            self.imports.append(ImportInfo(
                module=module,
                import_type="standard",
                line_number=node.lineno,
                is_external=is_external,
                names=[alias.asname or alias.name] if alias.asname else [],
            ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        level = node.level or 0
        module_name = node.module or ""
        names = [alias.name for alias in node.names]

        if level > 0:
            # Relative import
            resolved = self._resolve_relative(module_name, level)
            self.imports.append(ImportInfo(
                module=("." * level) + module_name if module_name else "." * level,
                import_type="relative",
                line_number=node.lineno,
                resolved_path=resolved,
                names=names,
                is_external=False,
            ))
        else:
            # Absolute import — check if it resolves to internal module
            is_external = self._is_external(module_name)
            import_type: str = "standard"
            resolved_path: str | None = None

            if not is_external and self.project_root is not None:
                resolved = self._resolve_absolute(module_name)
                if resolved:
                    resolved_path = resolved
                    import_type = "reexport" if self._is_init_reexport(module_name) else "standard"

            self.imports.append(ImportInfo(
                module=module_name,
                import_type=import_type,  # type: ignore[arg-type]
                line_number=node.lineno,
                resolved_path=resolved_path,
                names=names,
                is_external=is_external,
            ))

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        raw = self._extract_dynamic_import(node)
        if raw is not None:
            module_arg, raw_expr = raw
            self.imports.append(ImportInfo(
                module=module_arg,
                import_type="dynamic",
                line_number=node.lineno,
                raw_expression=raw_expr,
                resolved_path=None,
            ))
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _is_external(self, module: str) -> bool:
        """Return True if module is stdlib or presumed third-party."""
        if not module:
            return False
        top = module.split(".")[0]
        if top in self._stdlib:
            return True
        # Check if the module can be found relative to project_root
        if self.project_root is not None:
            if self._resolve_absolute(module):
                return False
        # Unknown module — treat as external
        return True

    def _resolve_relative(self, module_name: str, level: int) -> str | None:
        """Resolve a relative import to a file path string relative to project_root."""
        if self.project_root is None:
            return None

        # Start from the file's directory and go up `level - 1` levels
        base = self.file_path.parent
        for _ in range(level - 1):
            base = base.parent

        if module_name:
            candidate = base / Path(module_name.replace(".", "/"))
        else:
            candidate = base

        # Try as package (__init__.py) or direct module
        for suffix in ["", ".py"]:
            full = Path(str(candidate) + suffix) if suffix else candidate / "__init__.py"
            if full.exists():
                try:
                    return str(full.relative_to(self.project_root))
                except ValueError:
                    return str(full)

        # Check if candidate.py exists
        py_file = Path(str(candidate) + ".py")
        if py_file.exists():
            try:
                return str(py_file.relative_to(self.project_root))
            except ValueError:
                return str(py_file)

        return None

    def _resolve_absolute(self, module_name: str) -> str | None:
        """Resolve an absolute import to a project-relative path."""
        if self.project_root is None or not module_name:
            return None
        candidate = self.project_root / Path(module_name.replace(".", "/"))
        init_file = candidate / "__init__.py"
        py_file = Path(str(candidate) + ".py")
        if init_file.exists():
            try:
                return str(init_file.relative_to(self.project_root))
            except ValueError:
                return str(init_file)
        if py_file.exists():
            try:
                return str(py_file.relative_to(self.project_root))
            except ValueError:
                return str(py_file)
        return None

    def _is_init_reexport(self, module_name: str) -> bool:
        """Return True if module resolves to a package __init__.py."""
        if self.project_root is None or not module_name:
            return False
        candidate = self.project_root / Path(module_name.replace(".", "/"))
        return (candidate / "__init__.py").exists()

    def _extract_dynamic_import(self, node: ast.Call) -> tuple[str, str] | None:
        """Detect importlib.import_module() and __import__() calls.

        Returns (module_name, raw_expression) or None.
        """
        func = node.func

        # __import__("x")
        if isinstance(func, ast.Name) and func.id == "__import__":
            if node.args:
                module_arg = self._const_str(node.args[0])
                raw = ast.unparse(node)
                return (module_arg or "<dynamic>", raw)

        # importlib.import_module("x")
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "import_module"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
        ):
            if node.args:
                module_arg = self._const_str(node.args[0])
                raw = ast.unparse(node)
                return (module_arg or "<dynamic>", raw)

        return None

    @staticmethod
    def _const_str(node: ast.expr) -> str | None:
        """Extract string constant from AST node."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None


class _ExportVisitor(ast.NodeVisitor):
    """Extracts top-level exports, signatures, and class names."""

    def __init__(self) -> None:
        self.exports: list[str] = []
        self.signatures: list[str] = []
        self.classes: list[str] = []
        self._all_names: list[str] | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Only top-level (col_offset == 0) functions
        if node.col_offset == 0:
            self.exports.append(node.name)
            sig = self._format_signature(node)
            self.signatures.append(sig)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node.col_offset == 0:
            self.exports.append(node.name)
            sig = self._format_async_signature(node)
            self.signatures.append(sig)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.col_offset == 0:
            self.classes.append(node.name)
            self.exports.append(node.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Capture __all__ = [...] assignments."""
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    self._all_names = [
                        elt.value
                        for elt in node.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]
        self.generic_visit(node)

    def get_exports(self) -> list[str]:
        """Return __all__ if defined, otherwise all top-level names."""
        if self._all_names is not None:
            return self._all_names
        return self.exports

    @staticmethod
    def _format_signature(node: ast.FunctionDef) -> str:
        args = ast.unparse(node.args)
        return_part = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"def {node.name}({args}){return_part}"

    @staticmethod
    def _format_async_signature(node: ast.AsyncFunctionDef) -> str:
        args = ast.unparse(node.args)
        return_part = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"async def {node.name}({args}){return_part}"


class _DecoratorVisitor(ast.NodeVisitor):
    """Detects decorator-based registrations and __name__ == '__main__' guard."""

    def __init__(self, file_path: Path, project_root: Path | None) -> None:
        self.file_path = file_path
        self.project_root = project_root
        self.decorator_imports: list[ImportInfo] = []
        self.has_main_guard: bool = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_decorators(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_decorators(node)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        """Detect `if __name__ == "__main__":` guard."""
        if self._is_main_guard(node.test):
            self.has_main_guard = True
        self.generic_visit(node)

    def _check_decorators(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            name, attr = self._extract_decorator_info(decorator)
            if attr and attr in _REGISTRATION_DECORATORS:
                self.decorator_imports.append(ImportInfo(
                    module=name or attr,
                    import_type="decorator",
                    line_number=decorator.lineno,
                ))

    @staticmethod
    def _extract_decorator_info(decorator: ast.expr) -> tuple[str | None, str | None]:
        """Return (object_name, method_name) for attribute decorators."""
        if isinstance(decorator, ast.Attribute):
            obj = decorator.value
            attr = decorator.attr
            if isinstance(obj, ast.Name):
                return (obj.id, attr)
            return (None, attr)
        if isinstance(decorator, ast.Call):
            return _DecoratorVisitor._extract_decorator_info(decorator.func)
        if isinstance(decorator, ast.Name):
            return (decorator.id, decorator.id)
        return (None, None)

    @staticmethod
    def _is_main_guard(test: ast.expr) -> bool:
        """Return True if test is `__name__ == "__main__"`."""
        if not isinstance(test, ast.Compare):
            return False
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            return False
        if len(test.comparators) != 1:
            return False
        left = test.left
        right = test.comparators[0]
        # Both orderings: __name__ == "__main__" and "__main__" == __name__
        if (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(right, ast.Constant)
            and right.value == "__main__"
        ):
            return True
        if (
            isinstance(right, ast.Name)
            and right.id == "__name__"
            and isinstance(left, ast.Constant)
            and left.value == "__main__"
        ):
            return True
        return False


class PythonAdapter(LanguageAdapter):
    """Python AST adapter implementing the LanguageAdapter contract.

    Args:
        project_root: Root directory of the project, used for resolving
            relative and internal absolute imports via path-based resolution.
            Must be provided for accurate import resolution.
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".py"})

    def analyze(self, path: Path) -> FileAnalysis:
        """Parse a single Python file and return structured analysis.

        Args:
            path: Path to the Python source file.

        Returns:
            FileAnalysis with imports (list[ImportInfo]), exports, signatures,
            classes, and has_main_guard set appropriately.

        Raises:
            FileNotFoundError: If the file does not exist.
            SyntaxError: If the file cannot be parsed as valid Python.
        """
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        source = path.read_text(encoding="utf-8")

        if not source.strip():
            return FileAnalysis(
                path=str(path),
                language="python",
                imports=[],
                exports=[],
                signatures=[],
                classes=[],
                has_main_guard=False,
            )

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            raise

        # Collect imports
        import_visitor = _ImportVisitor(path, self._project_root)
        import_visitor.visit(tree)

        # Collect decorator registrations and main guard
        decorator_visitor = _DecoratorVisitor(path, self._project_root)
        decorator_visitor.visit(tree)

        # Collect exports, signatures, classes
        export_visitor = _ExportVisitor()
        export_visitor.visit(tree)

        all_imports = import_visitor.imports + decorator_visitor.decorator_imports

        return FileAnalysis(
            path=str(path),
            language="python",
            imports=all_imports,
            exports=export_visitor.get_exports(),
            signatures=export_visitor.signatures,
            classes=export_visitor.classes,
            has_main_guard=decorator_visitor.has_main_guard,
        )
