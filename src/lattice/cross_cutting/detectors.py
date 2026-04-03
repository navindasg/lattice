"""AST-based cross-cutting pattern detectors.

Four detector classes for Python source analysis:
- EventFlowDetector: finds event producer/consumer patterns
- SharedStateDetector: identifies module-level global objects
- ApiContractDetector: extracts HTTP route decorators
- PluginPointDetector: finds importlib.metadata entry_points usage

Each detector follows the same interface:
    detect(tree: ast.Module, relative_path: str) -> ResultType

EventFlowDetector returns an EventDetectionResult dataclass (frozen).
The other three return list[ModelType].
"""
import ast
from dataclasses import dataclass

from lattice.cross_cutting.schema import (
    ApiContract,
    CrossCuttingBlindSpot,
    EventFlow,
    PluginPoint,
    SharedState,
)

# Event emission method names
_EMIT_ATTRS = frozenset({"emit", "send", "dispatch", "publish", "fire", "trigger"})
# Event subscription method names
_SUBSCRIBE_ATTRS = frozenset({"on", "listen", "subscribe", "connect", "bind", "register"})
# Route decorator method names
_ROUTE_ATTRS = frozenset({"route", "get", "post", "put", "delete", "patch", "options", "head"})
# Framework import module prefixes
_FRAMEWORK_MAP = {"flask": "flask", "fastapi": "fastapi", "starlette": "fastapi"}


@dataclass(frozen=True)
class EventDetectionResult:
    """Result of EventFlowDetector.detect().

    producers: list of (event_name, line_number) tuples
    consumers: list of (event_name, line_number) tuples
    blind_spots: dynamic event names that could not be resolved
    """

    producers: list[tuple[str, int]]
    consumers: list[tuple[str, int]]
    blind_spots: list[CrossCuttingBlindSpot]


class _EventCallVisitor(ast.NodeVisitor):
    """AST visitor that finds event emit and subscribe calls."""

    def __init__(self, relative_path: str) -> None:
        self._relative_path = relative_path
        self.producers: list[tuple[str, int]] = []
        self.consumers: list[tuple[str, int]] = []
        self.blind_spots: list[CrossCuttingBlindSpot] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        """Visit a function call node."""
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in _EMIT_ATTRS and node.args:
                self._handle_call(node, attr, is_producer=True)
            elif attr in _SUBSCRIBE_ATTRS and node.args:
                self._handle_call(node, attr, is_producer=False)
        # Also detect Celery .delay() and .apply_async() as producer dispatch
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr in {"delay", "apply_async"}:
                self.producers.append((f"celery:{node.func.attr}", node.lineno))
        self.generic_visit(node)

    def _handle_call(self, node: ast.Call, attr: str, *, is_producer: bool) -> None:
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            event_name = first_arg.value
            if is_producer:
                self.producers.append((event_name, node.lineno))
            else:
                self.consumers.append((event_name, node.lineno))
        else:
            # Dynamic event name — cannot be resolved statically
            self.blind_spots.append(
                CrossCuttingBlindSpot(
                    file=self._relative_path,
                    line=node.lineno,
                    pattern_type="event_emitter",
                    reason="dynamic event name",
                )
            )


class EventFlowDetector:
    """Detects event producer and consumer patterns via AST analysis.

    Matches method calls like bus.emit("event.name") as producers and
    bus.on("event.name", handler) as consumers.

    Dynamic event names (variables, f-strings) produce CrossCuttingBlindSpot
    entries rather than false EventFlow matches.
    """

    def detect(self, tree: ast.Module, relative_path: str) -> EventDetectionResult:
        """Detect event producers and consumers in an AST module.

        Args:
            tree: Parsed AST module from ast.parse().
            relative_path: Source file path relative to project root.

        Returns:
            EventDetectionResult with producers, consumers, and blind_spots.
        """
        visitor = _EventCallVisitor(relative_path)
        visitor.visit(tree)
        return EventDetectionResult(
            producers=visitor.producers,
            consumers=visitor.consumers,
            blind_spots=visitor.blind_spots,
        )


class SharedStateDetector:
    """Detects module-level global objects shared across modules.

    Analyzes top-level ast.Assign nodes only (not function-local).
    Identifies:
    - Names ending in _registry, _cache, _store, Registry, Cache
      -> pattern_type="global_registry"
    - Module-level dict/list/set literals or constructors
      -> pattern_type="module_global"
    """

    _REGISTRY_SUFFIXES = ("_registry", "_cache", "_store", "Registry", "Cache", "Store")

    def detect(self, tree: ast.Module, relative_path: str) -> list[SharedState]:
        """Detect module-level shared state objects.

        Args:
            tree: Parsed AST module from ast.parse().
            relative_path: Source file path relative to project root.

        Returns:
            List of SharedState instances for each detected global.
            consumer_modules is always empty here; populated by the analyzer.
        """
        results: list[SharedState] = []

        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.Assign):
                continue
            # Only process simple Name targets (not tuple/list unpacking)
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id
                pattern_type = self._classify_pattern(name, node.value)
                if pattern_type is not None:
                    results.append(
                        SharedState(
                            object_name=name,
                            owner_module=relative_path,
                            pattern_type=pattern_type,
                        )
                    )
        return results

    def _classify_pattern(
        self, name: str, value: ast.expr
    ) -> str | None:
        """Determine if a module-level assignment is shared state.

        Returns pattern_type string or None if not shared state.
        """
        # Check name suffix for registry/cache patterns
        if any(name.endswith(suffix) for suffix in self._REGISTRY_SUFFIXES):
            return "global_registry"
        # Check value type for module_global patterns
        if isinstance(value, (ast.Dict, ast.List, ast.Set)):
            return "module_global"
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            if value.func.id in {"dict", "list", "set"}:
                return "module_global"
        return None


class ApiContractDetector:
    """Detects HTTP route declarations from Flask/FastAPI decorators.

    Walks FunctionDef and AsyncFunctionDef nodes looking for decorators
    that match route method names (route, get, post, put, etc.).

    Framework is detected from module-level import statements.
    """

    def detect(self, tree: ast.Module, relative_path: str) -> list[ApiContract]:
        """Detect HTTP route declarations in an AST module.

        Args:
            tree: Parsed AST module from ast.parse().
            relative_path: Source file path relative to project root.

        Returns:
            List of ApiContract instances for each decorated route.
        """
        framework = self._detect_framework(tree)
        results: list[ApiContract] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                contracts = self._extract_from_decorator(
                    decorator, node.name, relative_path, framework
                )
                results.extend(contracts)
        return results

    def _detect_framework(self, tree: ast.Module) -> str:
        """Detect web framework from module-level imports."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = ""
                if isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        module = alias.name
                        break
                prefix = module.split(".")[0].lower()
                if prefix in _FRAMEWORK_MAP:
                    return _FRAMEWORK_MAP[prefix]
        return "unknown"

    def _extract_from_decorator(
        self,
        decorator: ast.expr,
        func_name: str,
        relative_path: str,
        framework: str,
    ) -> list[ApiContract]:
        """Extract ApiContract entries from a single decorator node."""
        results: list[ApiContract] = []

        if not isinstance(decorator, ast.Call):
            return results
        func = decorator.func
        if not isinstance(func, ast.Attribute):
            return results
        attr = func.attr
        if attr not in _ROUTE_ATTRS:
            return results

        # Extract path from first positional argument
        if not decorator.args:
            return results
        first_arg = decorator.args[0]
        if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
            return results
        path = first_arg.value

        # Extract methods from keyword argument
        methods = self._extract_methods(decorator, attr)
        for method in methods:
            results.append(
                ApiContract(
                    method=method,
                    path=path,
                    handler_module=relative_path,
                    handler_function=func_name,
                    framework=framework,  # type: ignore[arg-type]
                )
            )
        return results

    def _extract_methods(self, decorator: ast.Call, attr: str) -> list[str]:
        """Extract HTTP method list from decorator arguments."""
        # Check for explicit methods= keyword
        for keyword in decorator.keywords:
            if keyword.arg == "methods" and isinstance(keyword.value, ast.List):
                methods = []
                for elt in keyword.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        methods.append(elt.value.upper())
                if methods:
                    return methods
        # Infer method from decorator name
        if attr == "route":
            return ["GET"]
        return [attr.upper()]


class PluginPointDetector:
    """Detects plugin/extension points using importlib.metadata or setuptools.

    Looks for:
    - importlib.metadata.entry_points(group=...) calls
    - pkg_resources.iter_entry_points(...) calls
    - setup(entry_points={...}) in setup.py style files
    """

    def detect(self, tree: ast.Module, relative_path: str) -> list[PluginPoint]:
        """Detect plugin point declarations in an AST module.

        Args:
            tree: Parsed AST module from ast.parse().
            relative_path: Source file path relative to project root.

        Returns:
            List of PluginPoint instances for each detected extension point.
        """
        results: list[PluginPoint] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            point = self._extract_plugin_point(node, relative_path)
            if point is not None:
                results.append(point)
        return results

    def _extract_plugin_point(
        self, node: ast.Call, relative_path: str
    ) -> PluginPoint | None:
        """Try to extract a PluginPoint from a Call node."""
        func = node.func

        # importlib.metadata.entry_points(group="...") or entry_points(group="...")
        if isinstance(func, ast.Attribute) and func.attr == "entry_points":
            group = self._extract_group_kwarg(node)
            if group is not None:
                return PluginPoint(
                    group=group,
                    name="entry_points",
                    target_module=relative_path,
                    pattern_type="importlib_metadata",
                )

        # pkg_resources.iter_entry_points("group") or iter_entry_points("group")
        if isinstance(func, ast.Attribute) and func.attr == "iter_entry_points":
            group = self._extract_positional_str(node)
            if group is not None:
                return PluginPoint(
                    group=group,
                    name="iter_entry_points",
                    target_module=relative_path,
                    pattern_type="setuptools_entry_points",
                )

        return None

    def _extract_group_kwarg(self, node: ast.Call) -> str | None:
        """Extract group= keyword argument value.

        Accepts both constant strings and variable names. For variable names,
        returns the variable name as a placeholder (e.g. "group" if
        entry_points(group=group) is used with a function parameter).
        """
        for keyword in node.keywords:
            if keyword.arg == "group":
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    return keyword.value.value
                # Dynamic group value (variable) — return the variable name as placeholder
                if isinstance(keyword.value, ast.Name):
                    return f"<{keyword.value.id}>"
                # Return a generic placeholder for other dynamic patterns
                return "<dynamic>"
        # Also check first positional argument
        if node.args and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, str):
                return node.args[0].value
        return None

    def _extract_positional_str(self, node: ast.Call) -> str | None:
        """Extract first positional string argument."""
        if node.args and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, str):
                return node.args[0].value
        return None
