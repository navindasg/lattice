"""Analysis models: ImportInfo, FileAnalysis, and GraphNode.

ImportInfo captures a single resolved import statement from a source file.
FileAnalysis captures the structured output of a single file parse.
GraphNode represents a node in the codebase dependency graph.

All models are frozen (immutable after construction). Use model_copy(update={...})
to derive modified instances.
"""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


_SUPPORTED_LANGUAGES = frozenset({"python", "typescript", "javascript"})

ImportType = Literal["standard", "relative", "reexport", "dynamic", "decorator", "config_ref"]


class ImportInfo(BaseModel):
    """Structured representation of a single import statement.

    Required fields: module, import_type, line_number.
    Optional fields default to None or empty list.

    import_type values:
        - "standard":   import os / from os import path
        - "relative":   from . import utils / from ..pkg import x
        - "reexport":   from package import name where package/__init__.py re-exports
        - "dynamic":    importlib.import_module("x") or __import__("x")
        - "decorator":  @app.route / @celery.task / @click.command
        - "config_ref": import via config file reference
    """

    module: str
    import_type: ImportType
    line_number: int
    resolved_path: str | None = None
    names: list[str] = Field(default_factory=list)
    is_external: bool = False
    raw_expression: str | None = None

    model_config = {"frozen": True}


class FileAnalysis(BaseModel):
    """Structured result of parsing a single source file.

    Required fields: path, language.
    All list fields default to empty; analyzed_at defaults to utcnow.
    has_main_guard signals presence of `if __name__ == "__main__"` for
    downstream entry point detection (Plan 03 EntryPointDetector).
    """

    path: str
    language: str
    imports: list[ImportInfo] = Field(default_factory=list)
    exports: list[str] = Field(default_factory=list)
    signatures: list[str] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)
    has_main_guard: bool = False
    analyzed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    model_config = {"frozen": True}


class GraphNode(BaseModel):
    """A node in the codebase dependency graph.

    The language field is validated against the supported set:
    {"python", "typescript", "javascript"}.
    """

    id: str
    path: str
    language: str
    edges: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}

    @field_validator("language")
    @classmethod
    def language_must_be_supported(cls, v: str) -> str:
        if v not in _SUPPORTED_LANGUAGES:
            raise ValueError(
                f"language must be one of {sorted(_SUPPORTED_LANGUAGES)}, got {v!r}"
            )
        return v
