"""Fleet data models for the agent fleet dispatcher.

All models are frozen Pydantic with model_config = {"frozen": True}.
Use model_copy(update={...}) for safe derived instances.

Models:
    Wave              — a parallelizable group of directories at the same topological depth
    WavePlan          — complete execution plan with all waves and run metadata
    AgentResult       — outcome of a single directory investigation by an LLM agent
    DirectoryContext  — all input context assembled for a single directory prompt
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from lattice.shadow.schema import DirDoc


class Wave(BaseModel):
    """A single wave in a fleet execution plan.

    All directories in a wave can be processed in parallel because their
    dependencies (outbound edges) have already been documented in earlier waves.

    Attributes:
        index: Zero-based wave sequence index (wave 0 = leaves, last wave = root).
        directories: Set of directory paths assigned to this wave.
        estimated_input_tokens: Approximate token cost for all directories in this wave.
    """

    index: int
    directories: frozenset[str]
    estimated_input_tokens: int = 0

    model_config = {"frozen": True}


class WavePlan(BaseModel):
    """Complete topological execution plan for a fleet run.

    Attributes:
        waves: Ordered list of Wave objects from leaf (index 0) to root (last index).
        total_estimated_tokens: Sum of estimated tokens across all waves.
        run_id: Unique identifier for this execution plan (used for DuckDB checkpointing).
    """

    waves: list[Wave]
    total_estimated_tokens: int = 0
    run_id: str

    model_config = {"frozen": True}


class AgentResult(BaseModel):
    """Outcome of a single directory investigation by an LLM fleet agent.

    Attributes:
        directory: Relative path of the directory that was investigated.
        failed: True if the agent failed after all retries.
        error: Error message if failed, None on success.
        dir_doc: Validated DirDoc written by the agent, None if failed.
        test_stubs: List of generated test stub dicts (from same LLM call as dir_doc).
        input_tokens: Actual input tokens consumed by the LLM call.
        output_tokens: Actual output tokens consumed by the LLM call.
    """

    directory: str
    failed: bool = False
    error: str | None = None
    dir_doc: DirDoc | None = None
    test_stubs: list[dict] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    model_config = {"frozen": True}


class DirectoryContext(BaseModel):
    """All input context assembled for a single directory prompt.

    This model bundles the inputs that PromptBuilder assembles before
    dispatching a directory to an LLM agent. Used for structured passing
    between the dispatcher and prompt builder.

    Attributes:
        directory: Relative path of the directory being investigated.
        files: List of file dicts with 'path' and 'content' keys.
        inbound_edges: List of directory paths that import files in this directory.
        outbound_edges: List of directory paths that files in this directory import.
        gap_entries: List of gap entry dicts (untested seams involving this directory).
        child_summaries: List of child directory summary dicts (summary + responsibilities).
        developer_hints: Developer-provided hints from existing _dir.md if present.
        is_entry_point: True if any file in this directory is a graph entry point.
    """

    directory: str
    files: list[dict] = Field(default_factory=list)
    inbound_edges: list[str] = Field(default_factory=list)
    outbound_edges: list[str] = Field(default_factory=list)
    gap_entries: list[dict] = Field(default_factory=list)
    child_summaries: list[dict] = Field(default_factory=list)
    developer_hints: list[str] = Field(default_factory=list)
    is_entry_point: bool = False

    model_config = {"frozen": True}
