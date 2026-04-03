"""Shadow tree writer — writes DirDoc instances to _dir.md files.

The shadow path convention is:
    {agent_docs_root}/{doc.directory}/_dir.md

All machine-readable fields go into YAML frontmatter.
Human-readable fields (summary, responsibilities, developer_hints, child_refs)
go into the Markdown body as fixed-header sections.

Pitfall: last_analyzed must be stored as an ISO 8601 string (not a datetime
object) so python-frontmatter doesn't mangle timezone info.
"""
from pathlib import Path

import frontmatter

from lattice.shadow.schema import DirDoc


def write_dir_doc(doc: DirDoc, agent_docs_root: Path) -> Path:
    """Write a DirDoc to its shadow path under agent_docs_root.

    The shadow path is:
        {agent_docs_root}/{doc.directory}/_dir.md

    Creates parent directories as needed.

    Args:
        doc: Validated DirDoc instance to write.
        agent_docs_root: Root of the .agent-docs shadow tree.

    Returns:
        Path to the written _dir.md file.
    """
    shadow_path = agent_docs_root / doc.directory / "_dir.md"
    shadow_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = _doc_to_frontmatter_dict(doc)
    body = _doc_to_markdown_body(doc)

    post = frontmatter.Post(body, **metadata)
    content = frontmatter.dumps(post)

    shadow_path.write_text(content, encoding="utf-8")
    return shadow_path


def _doc_to_frontmatter_dict(doc: DirDoc) -> dict:
    """Extract machine-readable fields as a dict for YAML frontmatter.

    last_analyzed is stored as ISO 8601 string to preserve timezone info
    through python-frontmatter's YAML round-trip.
    """
    result = {
        "directory": doc.directory,
        "confidence": doc.confidence,
        "source": doc.source,
        "confidence_factors": list(doc.confidence_factors),
        "stale": doc.stale,
        "last_analyzed": doc.last_analyzed.isoformat(),
        "static_analysis_limits": doc.static_analysis_limits.model_dump(),
        "gap_summary": doc.gap_summary.model_dump(),
    }
    if doc.integration_points:
        result["integration_points"] = [dict(ip) for ip in doc.integration_points]
    return result


def _doc_to_markdown_body(doc: DirDoc) -> str:
    """Render DirDoc human-readable fields as fixed Markdown sections.

    All four sections are always included even when empty, ensuring
    parse_dir_doc can rely on header presence for parsing.
    """
    sections = ["## Summary", "", doc.summary or "", ""]
    sections += ["## Key Responsibilities", ""]
    sections += [f"- {r}" for r in doc.responsibilities] or [""]
    sections += ["", "## Developer Hints", ""]
    sections += [f"- {h}" for h in doc.developer_hints] or [""]
    sections += ["", "## Child Docs", ""]
    sections += [f"- {c}" for c in doc.child_refs] or [""]
    if doc.integration_points:
        sections += ["", "## Integration Points", ""]
        for ip in doc.integration_points:
            edge = ip.get("edge", "unknown")
            status = ip.get("status", "UNTESTED")
            test_file = ip.get("test_file")
            if test_file:
                sections.append(f"- [{status}] `{edge}` ({test_file})")
            else:
                sections.append(f"- [{status}] `{edge}`")
    return "\n".join(sections)
