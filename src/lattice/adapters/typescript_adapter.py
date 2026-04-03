"""TypeScriptAdapter — subprocess bridge to ts-morph Node.js parser (SA-03).

Implements the LanguageAdapter ABC for TypeScript and JavaScript files by
delegating AST parsing to a Node.js subprocess running the ts-morph parser
script (ts_parser/parse_imports.js).

Architecture:
    Python side  →  subprocess.run(["node", "parse_imports.js", file_path])
                 →  JSON output on stdout
                 ←  Parsed into FileAnalysis with list[ImportInfo]

The Node.js subprocess handles all TypeScript compiler complexity (ES modules,
CommonJS require, dynamic import(), tsconfig path aliases, barrel exports).
Python side is intentionally thin: spawn, parse JSON, convert to ImportInfo.

Usage:
    adapter = TypeScriptAdapter(project_root=Path("/path/to/project"))
    result = adapter.analyze(Path("/path/to/project/src/index.ts"))

Subprocess timeout: 30 seconds per file.
Node.js availability: checked at __init__ time (fail fast, clear error message).
"""
import json
import subprocess
from pathlib import Path
from typing import Any

from lattice.adapters.base import LanguageAdapter
from lattice.models.analysis import FileAnalysis, ImportInfo, ImportType


# Extensions handled by this adapter
_SUPPORTED_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx"})

# Languages by extension
_TYPESCRIPT_EXTENSIONS = frozenset({".ts", ".tsx"})
_JAVASCRIPT_EXTENSIONS = frozenset({".js", ".jsx"})

# Subprocess timeout in seconds
_SUBPROCESS_TIMEOUT = 30

# Path to the ts-morph parser script, relative to this file's location:
#   adapters/typescript_adapter.py
#   -> adapters/
#   -> src/lattice/
#   -> src/
#   -> packages/lattice/
#   -> packages/lattice/ts_parser/parse_imports.js
_TS_PARSER_SCRIPT = (
    Path(__file__).parent.parent.parent.parent / "ts_parser" / "parse_imports.js"
)


def _import_type_from_str(raw: str) -> ImportType:
    """Convert raw import_type string from JSON to typed ImportType.

    Defaults to 'standard' for unknown values to avoid hard failures.
    """
    valid: set[str] = {
        "standard", "relative", "reexport", "dynamic", "decorator", "config_ref"
    }
    if raw in valid:
        return raw  # type: ignore[return-value]
    return "standard"


def _convert_import(raw: dict[str, Any]) -> ImportInfo:
    """Convert a raw import dict from JSON output to an ImportInfo instance."""
    return ImportInfo(
        module=raw.get("module", ""),
        import_type=_import_type_from_str(raw.get("import_type", "standard")),
        line_number=int(raw.get("line_number", 0)),
        resolved_path=raw.get("resolved_path") or None,
        names=list(raw.get("names", [])),
        is_external=bool(raw.get("is_external", False)),
        raw_expression=raw.get("raw_expression") or None,
    )


class TypeScriptAdapter(LanguageAdapter):
    """TypeScript/JavaScript adapter via ts-morph Node.js subprocess bridge.

    Args:
        project_root: Root directory of the project, used for language
            detection and optional tsconfig.json resolution.

    Raises:
        RuntimeError: If Node.js is not available on PATH at construction time.
        RuntimeError: If the ts-morph parser script is not found.
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._ts_parser_script = _TS_PARSER_SCRIPT

        # Fail fast: check Node.js availability at init time
        try:
            result = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Node.js returned non-zero exit code: {result.stderr.strip()}"
                )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Node.js is required for TypeScript analysis but 'node' was not "
                "found on PATH. Install Node.js (https://nodejs.org/) and ensure "
                "it is on your PATH."
            ) from exc

        # Verify the parser script exists
        if not self._ts_parser_script.exists():
            raise RuntimeError(
                f"ts-morph parser script not found at: {self._ts_parser_script}. "
                "Run 'npm install' in the ts_parser directory."
            )

    @property
    def supported_extensions(self) -> frozenset[str]:
        """File extensions handled by this adapter."""
        return _SUPPORTED_EXTENSIONS

    def analyze(self, path: Path) -> FileAnalysis:
        """Parse a single TypeScript or JavaScript file via Node.js subprocess.

        Args:
            path: Path to the source file (.ts, .tsx, .js, or .jsx).

        Returns:
            FileAnalysis with imports (list[ImportInfo]), exports, and
            language set to "typescript" or "javascript".

        Raises:
            RuntimeError: If the file does not exist, the subprocess fails,
                or the JSON output cannot be parsed.
        """
        if not path.exists():
            raise RuntimeError(
                f"File not found (nonexistent): {path}"
            )

        # Determine language from extension
        suffix = path.suffix.lower()
        language = "typescript" if suffix in _TYPESCRIPT_EXTENSIONS else "javascript"

        # Build subprocess command
        cmd = ["node", str(self._ts_parser_script), str(path)]

        # Optionally pass tsconfig.json if found in project_root
        tsconfig = self._project_root / "tsconfig.json"
        if tsconfig.exists():
            cmd.append(str(tsconfig))

        # Spawn the Node.js parser
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"TypeScript parser timed out after {_SUBPROCESS_TIMEOUT}s for: {path}"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"TypeScript parser failed for {path}:\n{result.stderr.strip()}"
            )

        # Parse JSON output
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"TypeScript parser returned invalid JSON for {path}: {exc}"
            ) from exc

        # Convert imports
        raw_imports: list[dict[str, Any]] = data.get("imports", [])
        imports = [_convert_import(imp) for imp in raw_imports]

        # Convert exports (flat list of strings)
        exports: list[str] = [str(e) for e in data.get("exports", [])]

        # Compute relative path from project_root if possible
        try:
            relative_path = str(path.relative_to(self._project_root))
        except ValueError:
            relative_path = str(path)

        return FileAnalysis(
            path=relative_path,
            language=language,
            imports=imports,
            exports=exports,
            signatures=[],
            classes=[],
        )
