"""Cross-cutting analysis writer — writes ProjectDoc instances to _project.md.

Mirrors the write_dir_doc() pattern from lattice/shadow/writer.py:
- YAML frontmatter: machine-readable counts and metadata
- Markdown body: human-readable sections for each pattern category

The _project.md file is written at:
    {agent_docs_root}/_project.md

Sections always present (empty sections use "_No ... detected._" placeholder):
- ## API Contracts
- ## Event Flows
- ## Shared State
- ## Plugin / Extension Points
- ## Blind Spots
"""
from pathlib import Path

import frontmatter

from lattice.cross_cutting.schema import ProjectDoc


def write_project_doc(doc: ProjectDoc, agent_docs_root: Path) -> Path:
    """Write a ProjectDoc to _project.md under agent_docs_root.

    Args:
        doc: Validated ProjectDoc instance to write.
        agent_docs_root: Root of the .agent-docs shadow tree.

    Returns:
        Path to the written _project.md file.
    """
    output_path = agent_docs_root / "_project.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = _doc_to_frontmatter_dict(doc)
    body = _doc_to_markdown_body(doc)

    post = frontmatter.Post(body, **metadata)
    content = frontmatter.dumps(post)

    output_path.write_text(content, encoding="utf-8")
    return output_path


def parse_project_doc(path: Path) -> ProjectDoc:
    """Read a _project.md file and return a validated ProjectDoc.

    Useful for round-trip testing and future incremental updates.

    Args:
        path: Path to a _project.md file.

    Returns:
        Validated ProjectDoc instance.

    Raises:
        ValidationError: If the file content does not match the schema.
        FileNotFoundError: If the file does not exist.
    """
    post = frontmatter.load(str(path))
    data = dict(post.metadata)
    return ProjectDoc.model_validate(data)


def _doc_to_frontmatter_dict(doc: ProjectDoc) -> dict:
    """Extract machine-readable fields for YAML frontmatter.

    Stores counts alongside analyzed_at for quick scanning.
    """
    return {
        "analyzed_at": doc.analyzed_at,
        "event_flow_count": len(doc.event_flows),
        "shared_state_count": len(doc.shared_state),
        "api_contract_count": len(doc.api_contracts),
        "plugin_point_count": len(doc.plugin_points),
        "blind_spot_count": len(doc.blind_spots),
    }


def _doc_to_markdown_body(doc: ProjectDoc) -> str:
    """Render ProjectDoc pattern sections as fixed Markdown headers.

    All sections are always included even when empty to ensure stable
    section presence for downstream parsers and agents.
    """
    sections: list[str] = []

    # API Contracts — table format
    sections.append("## API Contracts")
    sections.append("")
    if doc.api_contracts:
        sections.append("| Method | Path | Handler | Framework |")
        sections.append("|--------|------|---------|-----------|")
        for c in doc.api_contracts:
            handler = c.handler_function or c.handler_module
            sections.append(f"| {c.method} | {c.path} | {handler} | {c.framework} |")
    else:
        sections.append("_No API contracts detected._")
    sections.append("")

    # Event Flows — bullet list
    sections.append("## Event Flows")
    sections.append("")
    if doc.event_flows:
        for f in doc.event_flows:
            consumer = f.consumer_module or "unknown"
            sections.append(
                f"- `{f.event_name}`: `{f.producer_module}` -> `{consumer}` ({f.pattern_type})"
            )
    else:
        sections.append("_No event flows detected._")
    sections.append("")

    # Shared State — bullet list
    sections.append("## Shared State")
    sections.append("")
    if doc.shared_state:
        for s in doc.shared_state:
            consumers = ", ".join(s.consumer_modules) if s.consumer_modules else "none"
            sections.append(
                f"- `{s.object_name}` in `{s.owner_module}` ({s.pattern_type})"
                f" — consumers: {consumers}"
            )
    else:
        sections.append("_No shared state detected._")
    sections.append("")

    # Plugin / Extension Points — bullet list
    sections.append("## Plugin / Extension Points")
    sections.append("")
    if doc.plugin_points:
        for p in doc.plugin_points:
            sections.append(
                f"- group `{p.group}` in `{p.target_module}` ({p.pattern_type})"
            )
    else:
        sections.append("_No plugin points detected._")
    sections.append("")

    # Blind Spots — bullet list
    sections.append("## Blind Spots")
    sections.append("")
    if doc.blind_spots:
        for b in doc.blind_spots:
            sections.append(f"- `{b.file}:{b.line}` ({b.pattern_type}): {b.reason}")
    else:
        sections.append("_No blind spots detected._")

    return "\n".join(sections)
