"""Test stubs for write_project_doc — RED phase (Wave 0).

Imports from lattice.cross_cutting.writer which does not yet exist.
These tests will fail with ImportError until Wave 1 implements the writer.
"""
from datetime import datetime, timezone
from pathlib import Path

import frontmatter
import pytest

from lattice.cross_cutting.schema import ApiContract, EventFlow, ProjectDoc
from lattice.cross_cutting.writer import parse_project_doc, write_project_doc


class TestWriteProjectDoc:
    def test_writes_file_with_yaml_frontmatter(self, tmp_path):
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
        )
        output_path = write_project_doc(doc, tmp_path)
        assert output_path.exists()
        content = output_path.read_text()
        assert "---" in content
        assert "analyzed_at" in content

    def test_body_contains_all_sections(self, tmp_path):
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
        )
        output_path = write_project_doc(doc, tmp_path)
        content = output_path.read_text()
        assert "## API Contracts" in content
        assert "## Event Flows" in content
        assert "## Shared State" in content
        assert "## Plugin / Extension Points" in content
        assert "## Blind Spots" in content

    def test_empty_project_doc_produces_valid_file(self, tmp_path):
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
        )
        output_path = write_project_doc(doc, tmp_path)
        content = output_path.read_text()
        # Empty sections use placeholders
        assert "_No" in content or "No" in content

    def test_round_trips_through_parse(self, tmp_path):
        flow = EventFlow(
            event_name="user.created",
            producer_module="events/emitter.py",
            consumer_module="events/handlers.py",
            pattern_type="event_emitter",
            producer_line=28,
        )
        contract = ApiContract(
            method="GET",
            path="/health",
            handler_module="api/routes.py",
            framework="flask",
        )
        doc = ProjectDoc(
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            event_flows=[flow],
            api_contracts=[contract],
        )
        output_path = write_project_doc(doc, tmp_path)
        parsed = frontmatter.load(str(output_path))
        assert parsed["analyzed_at"] is not None
        assert parsed["event_flow_count"] == 1
        assert parsed["api_contract_count"] == 1
