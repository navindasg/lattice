"""Core logic for the map:hint, map:correct, and map:skip CLI commands.

Stores developer hints for a directory in .agent-docs/_hints.json.
Writes developer corrections back to _dir.md via the shadow writer.

Exports:
    _map_hint_impl  — store hint/idk/expand/skip entries in _hints.json
    _map_correct_impl — update _dir.md summary or responsibilities
    _map_skip_impl  — mark a directory as low-priority skip
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _iso_now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _read_hints(hints_path: Path) -> dict[str, list[dict]]:
    """Read _hints.json from disk, returning empty dict on missing/corrupt file."""
    if hints_path.exists():
        try:
            return json.loads(hints_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_hints(hints: dict[str, list[dict]], hints_path: Path, tmp_path: Path) -> None:
    """Atomically write hints dict to disk via tmp file and os.replace()."""
    tmp_path.write_text(json.dumps(hints, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp_path), str(hints_path))


def _map_hint_impl(
    target: Path,
    directory: str,
    hint_text: str | None,
    hint_type: str = "hint",
) -> dict:
    """Store a developer hint for *directory* in the project at *target*.

    Creates .agent-docs/ if it does not exist.
    Reads existing _hints.json and applies deduplication/upsert rules.
    Writes atomically via a .tmp file and os.replace().

    Deduplication rules:
    - hint/expand types: skip if entry with same text already exists.
    - idk/skip types: upsert — remove existing entry of same type before appending.
    - Non-IDK hint clears all existing IDK entries for that directory.
    - Backward compat: entries without 'type' are treated as type='hint'.

    Args:
        target: Path to the project root directory.
        directory: Relative directory path the hint applies to.
        hint_text: Free-form hint text; None for idk/skip types.
        hint_type: One of "hint", "idk", "expand", "skip". Defaults to "hint".

    Returns:
        {"directory": directory, "hint_count": <int>}
    """
    agent_docs = target / ".agent-docs"
    agent_docs.mkdir(parents=True, exist_ok=True)

    hints_path = agent_docs / "_hints.json"
    tmp_path = agent_docs / "_hints.json.tmp"

    hints = _read_hints(hints_path)
    existing: list[dict] = hints.get(directory, [])

    # Deduplication for hint/expand: skip if same text already present
    if hint_type in ("hint", "expand") and hint_text is not None:
        for existing_entry in existing:
            if existing_entry.get("text") == hint_text:
                return {"directory": directory, "hint_count": len(existing)}

    # Non-IDK hints clear all existing IDK entries for this directory
    if hint_type != "idk":
        existing = [e for e in existing if e.get("type", "hint") != "idk"]

    # Upsert for idk/skip: remove existing entries of same type
    if hint_type in ("idk", "skip"):
        existing = [e for e in existing if e.get("type", "hint") != hint_type]

    # Build the new entry
    entry: dict[str, Any] = {
        "type": hint_type,
        "stored_at": _iso_now(),
    }
    if hint_text is not None:
        entry["text"] = hint_text

    updated = [*existing, entry]
    hints = {**hints, directory: updated}

    _write_hints(hints, hints_path, tmp_path)

    return {
        "directory": directory,
        "hint_count": len(updated),
    }


def _map_correct_impl(
    target: Path,
    directory: str,
    field: str,
    value: str,
) -> dict:
    """Update a _dir.md field with developer-supplied correction.

    Validates field name, reads existing _dir.md, applies correction with
    confidence=1.0 and source=developer, writes back, and records an audit
    entry in _hints.json.

    Args:
        target: Path to the project root directory.
        directory: Relative directory path to correct.
        field: One of "summary" or "responsibilities".
        value: New value as a string. For responsibilities, accepts JSON array
               or comma-separated list.

    Returns:
        {"directory": directory, "field": field, "confidence": 1.0, "source": "developer"}

    Raises:
        ValueError: If field is not "summary" or "responsibilities".
        FileNotFoundError: If no _dir.md exists for directory.
    """
    from lattice.shadow.reader import parse_dir_doc
    from lattice.shadow.writer import write_dir_doc

    correctable_fields = {"summary", "responsibilities"}
    if field not in correctable_fields:
        raise ValueError(
            f"Field '{field}' is not correctable. Use: summary, responsibilities"
        )

    agent_docs_root = target / ".agent-docs"
    dir_md = agent_docs_root / directory / "_dir.md"

    if not dir_md.exists():
        raise FileNotFoundError(
            "No documentation found — run `map:doc` first"
        )

    doc = parse_dir_doc(dir_md)

    if field == "summary":
        update: dict[str, Any] = {
            "summary": value,
            "confidence": 1.0,
            "source": "developer",
        }
    else:  # responsibilities
        try:
            parsed_list = json.loads(value)
            if not isinstance(parsed_list, list):
                raise ValueError("not a list")
        except (json.JSONDecodeError, ValueError):
            parsed_list = [item.strip() for item in value.split(",") if item.strip()]
        update = {
            "responsibilities": parsed_list,
            "confidence": 1.0,
            "source": "developer",
        }

    corrected = doc.model_copy(update=update)
    write_dir_doc(corrected, agent_docs_root)

    # Record audit entry in _hints.json
    hints_path = agent_docs_root / "_hints.json"
    tmp_path = agent_docs_root / "_hints.json.tmp"
    hints = _read_hints(hints_path)
    existing = hints.get(directory, [])
    audit_entry: dict[str, Any] = {
        "type": "correct",
        "field": field,
        "value": value,
        "stored_at": _iso_now(),
    }
    hints = {**hints, directory: [*existing, audit_entry]}
    _write_hints(hints, hints_path, tmp_path)

    return {
        "directory": directory,
        "field": field,
        "confidence": 1.0,
        "source": "developer",
    }


def _map_skip_impl(target: Path, directory: str) -> dict:
    """Mark a directory as low-priority (skip) in _hints.json.

    Uses _map_hint_impl with hint_type="skip" for upsert semantics.

    Args:
        target: Path to the project root directory.
        directory: Relative directory path to mark as skip.

    Returns:
        {"directory": directory, "skipped": True}
    """
    _map_hint_impl(target, directory, hint_text=None, hint_type="skip")
    return {"directory": directory, "skipped": True}
