"""Shadow tree reader — parses _dir.md files and traverses the shadow tree.

parse_dir_doc: Reads a single _dir.md file and returns a validated DirDoc.
traverse: Walks an agent_docs_root, collecting all _dir.md files sorted by
          confidence ascending with stale-first priority within same band.

Pitfall: python-frontmatter may return last_analyzed as a datetime object
directly from YAML parsing. Always coerce to string via str() then parse
with datetime.fromisoformat() to guarantee timezone preservation.
"""
from datetime import datetime
from pathlib import Path

import frontmatter
import structlog

from lattice.shadow.schema import DirDoc, GapSummary, StaticAnalysisLimits
from lattice.shadow.staleness import is_stale

logger = structlog.get_logger()


def parse_dir_doc(path: Path) -> DirDoc:
    """Parse a _dir.md file and return a validated DirDoc.

    Extracts YAML frontmatter for machine-readable fields and parses the
    Markdown body for human-readable sections.

    Args:
        path: Path to a _dir.md file.

    Returns:
        Validated DirDoc instance with all fields populated.

    Raises:
        ValidationError: If frontmatter fields fail DirDoc validation.
        Exception: If the file cannot be parsed (caller should handle).
    """
    post = frontmatter.load(str(path))
    metadata = dict(post.metadata)

    # Coerce last_analyzed to string then parse to guarantee timezone preservation
    raw_last_analyzed = metadata.pop("last_analyzed")
    last_analyzed = datetime.fromisoformat(str(raw_last_analyzed))

    # Reconstruct nested models from dicts
    sal_dict = metadata.pop("static_analysis_limits", {})
    gap_dict = metadata.pop("gap_summary", {})
    static_analysis_limits = StaticAnalysisLimits(**sal_dict) if sal_dict else StaticAnalysisLimits()
    gap_summary = GapSummary(**gap_dict) if gap_dict else GapSummary()
    integration_points = metadata.pop("integration_points", [])

    body_sections = _parse_body_sections(post.content)

    return DirDoc(
        **metadata,
        last_analyzed=last_analyzed,
        static_analysis_limits=static_analysis_limits,
        gap_summary=gap_summary,
        integration_points=integration_points,
        **body_sections,
    )


def _parse_body_sections(body: str) -> dict:
    """Parse fixed Markdown sections from a _dir.md body.

    Splits on '## ' headers, mapping:
        'Summary'            -> summary (str)
        'Key Responsibilities' -> responsibilities (list[str] from bullet lines)
        'Developer Hints'    -> developer_hints (list[str] from bullet lines)
        'Child Docs'         -> child_refs (list[str] from bullet lines)

    Missing sections default to empty values, not errors.

    Args:
        body: Markdown body string (post.content from python-frontmatter).

    Returns:
        Dict with keys: summary, responsibilities, developer_hints, child_refs.
    """
    sections: dict[str, str] = {}
    current_header: str | None = None
    current_lines: list[str] = []

    for line in body.splitlines():
        if line.startswith("## "):
            if current_header is not None:
                sections[current_header] = "\n".join(current_lines).strip()
            current_header = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_header is not None:
        sections[current_header] = "\n".join(current_lines).strip()

    def parse_bullets(text: str) -> list[str]:
        lines = text.splitlines()
        return [ln.lstrip("- ").strip() for ln in lines if ln.strip().startswith("- ")]

    return {
        "summary": sections.get("Summary", ""),
        "responsibilities": parse_bullets(sections.get("Key Responsibilities", "")),
        "developer_hints": parse_bullets(sections.get("Developer Hints", "")),
        "child_refs": parse_bullets(sections.get("Child Docs", "")),
    }


def traverse(agent_docs_root: Path, project_root: Path) -> list[DirDoc]:
    """Collect all _dir.md files under agent_docs_root, sorted by confidence.

    Sort order:
        Primary:   confidence ascending (lowest confidence first — needs most attention)
        Secondary: stale=True before stale=False within the same confidence band

    Corrupt _dir.md files are logged and skipped without crashing.

    Args:
        agent_docs_root: Root of the shadow tree (.agent-docs directory).
        project_root: Git repository root for staleness detection.

    Returns:
        List of DirDoc instances sorted by (confidence, not stale).
    """
    docs: list[DirDoc] = []

    for dir_md_path in agent_docs_root.rglob("_dir.md"):
        try:
            doc = parse_dir_doc(dir_md_path)
        except Exception as exc:
            logger.warning(
                "skipping corrupt _dir.md",
                path=str(dir_md_path),
                error=str(exc),
            )
            continue

        stale = is_stale(doc, project_root)
        if stale != doc.stale:
            doc = doc.model_copy(update={"stale": stale})

        docs.append(doc)

    return sorted(docs, key=lambda d: (d.confidence, not d.stale))
