"""Cross-cutting analysis Pydantic schema models.

Provides frozen Pydantic models for project-level topology analysis:
- EventFlow: producer-consumer pairs for event-driven patterns
- SharedState: module-level global objects shared across modules
- ApiContract: HTTP route declarations extracted from decorators
- PluginPoint: setuptools/importlib.metadata extension points
- CrossCuttingBlindSpot: patterns that could not be statically resolved
- ProjectDoc: top-level container for a full cross-cutting analysis

All models use model_config = {"frozen": True} following the DirDoc pattern.
ProjectDoc stores analyzed_at as ISO 8601 string (not datetime) to prevent
python-frontmatter YAML timezone mangling — same decision as DirDoc.
"""
from typing import Literal

from pydantic import BaseModel, Field


class EventFlow(BaseModel):
    """A detected producer-consumer event relationship.

    Maps an event name to the module that emits it and the module that
    subscribes to it. consumer_module may be None if only a producer
    was found in the analyzed source.
    """

    event_name: str
    producer_module: str
    consumer_module: str
    pattern_type: Literal["celery_task", "event_emitter", "callback_registry", "custom_pubsub"]
    producer_line: int
    consumer_line: int | None = None

    model_config = {"frozen": True}


class SharedState(BaseModel):
    """A module-level global object shared across modules.

    owner_module is the file that declares the object.
    consumer_modules is populated by the analyzer after scanning all files.
    """

    object_name: str
    owner_module: str
    consumer_modules: list[str] = Field(default_factory=list)
    pattern_type: Literal["singleton", "global_registry", "shared_config", "module_global"]

    model_config = {"frozen": True}


class ApiContract(BaseModel):
    """An HTTP route declaration extracted from a route decorator.

    handler_function is the decorated function name.
    framework is detected from module-level imports.
    """

    method: str
    path: str
    handler_module: str
    handler_function: str | None = None
    framework: Literal["flask", "fastapi", "express", "unknown"] = "unknown"

    model_config = {"frozen": True}


class PluginPoint(BaseModel):
    """A plugin/extension point using setuptools or importlib.metadata.

    group is the entry_points group name.
    name is the plugin name (may be a placeholder for dynamic patterns).
    target_module is the file where the plugin point is declared.
    """

    group: str
    name: str
    target_module: str
    pattern_type: Literal["setuptools_entry_points", "importlib_metadata", "explicit_registry"]

    model_config = {"frozen": True}


class CrossCuttingBlindSpot(BaseModel):
    """A cross-cutting pattern that could not be statically resolved.

    Consistent with the _graph.json blind_spots pattern: records what
    is known (file, line, pattern type) and why it was unresolvable.
    """

    file: str
    line: int
    pattern_type: str
    reason: str

    model_config = {"frozen": True}


class ProjectDoc(BaseModel):
    """Top-level container for a full project cross-cutting analysis.

    Stored as YAML frontmatter + Markdown body in .agent-docs/_project.md.

    analyzed_at is an ISO 8601 string (not datetime) to prevent
    python-frontmatter YAML timezone mangling on round-trip.
    """

    analyzed_at: str
    event_flows: list[EventFlow] = Field(default_factory=list)
    shared_state: list[SharedState] = Field(default_factory=list)
    api_contracts: list[ApiContract] = Field(default_factory=list)
    plugin_points: list[PluginPoint] = Field(default_factory=list)
    blind_spots: list[CrossCuttingBlindSpot] = Field(default_factory=list)

    model_config = {"frozen": True}
