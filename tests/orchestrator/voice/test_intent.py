"""Tests for lattice.orchestrator.voice.intent — IntentClassifier."""
import pytest

from lattice.orchestrator.voice.intent import IntentClassifier
from lattice.orchestrator.voice.models import IntentResult
from tests.orchestrator.voice.fixtures.transcripts import TRANSCRIPT_FIXTURES


@pytest.fixture()
def classifier() -> IntentClassifier:
    return IntentClassifier()


class TestIntentClassifierCategories:
    def test_status_query_classification(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("what's the status")
        assert result.category == "status_query"
        assert isinstance(result, IntentResult)

    def test_mapper_command_classification(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("map the auth directory")
        assert result.category == "mapper_command"

    def test_task_dispatch_classification(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("start working on the auth refactor")
        assert result.category == "task_dispatch"

    def test_context_injection_classification(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("tell instance one about SAML")
        assert result.category == "context_injection"

    def test_unrecognized_classification(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("asdfghjkl gibberish xyz")
        assert result.category == "unrecognized"

    def test_unrecognized_has_zero_confidence(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("asdfghjkl gibberish xyz")
        assert result.confidence == 0.0

    def test_recognized_has_nonzero_confidence(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("what's the status")
        assert result.confidence > 0.0

    def test_transcript_preserved_in_result(self, classifier: IntentClassifier) -> None:
        utterance = "map the auth directory"
        result = classifier.classify(utterance)
        assert result.transcript == utterance


class TestIntentClassifierSlotExtraction:
    def test_mapper_command_extracts_target(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("map the auth directory")
        assert "target" in result.extracted
        assert result.extracted["target"] != ""

    def test_mapper_command_no_target_has_empty_extracted(
        self, classifier: IntentClassifier
    ) -> None:
        result = classifier.classify("map status")
        # Should still classify correctly even if target extraction fails
        assert result.category == "mapper_command"


class TestIntentClassifierFixtures:
    @pytest.mark.parametrize("utterance,expected_category", TRANSCRIPT_FIXTURES)
    def test_fixture_transcript_classified_correctly(
        self,
        classifier: IntentClassifier,
        utterance: str,
        expected_category: str,
    ) -> None:
        result = classifier.classify(utterance)
        assert result.category == expected_category, (
            f"Expected '{expected_category}' for '{utterance}', got '{result.category}'"
        )


class TestExternalFetchIntent:
    def test_look_up_classifies_as_external_fetch(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("look up SAML spec")
        assert result.category == "external_fetch"

    def test_look_up_extracts_query(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("look up SAML spec")
        assert result.extracted.get("query") == "SAML spec"

    def test_search_for_classifies_as_external_fetch(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("search for python async patterns")
        assert result.category == "external_fetch"

    def test_search_for_extracts_query(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("search for python async patterns")
        assert result.extracted.get("query") == "python async patterns"

    def test_fetch_latest_issues_classifies_as_external_fetch(
        self, classifier: IntentClassifier
    ) -> None:
        result = classifier.classify("fetch the latest issues")
        assert result.category == "external_fetch"

    def test_check_github_classifies_as_external_fetch(
        self, classifier: IntentClassifier
    ) -> None:
        result = classifier.classify("check the github status")
        assert result.category == "external_fetch"

    def test_check_mattermost_classifies_as_external_fetch(
        self, classifier: IntentClassifier
    ) -> None:
        result = classifier.classify("check the mattermost channel")
        assert result.category == "external_fetch"

    def test_get_me_ci_status_classifies_as_external_fetch(
        self, classifier: IntentClassifier
    ) -> None:
        result = classifier.classify("get me the CI status")
        assert result.category == "external_fetch"

    def test_existing_mapper_command_no_regression(
        self, classifier: IntentClassifier
    ) -> None:
        """'map the auth directory' still classifies as mapper_command (no regression)."""
        result = classifier.classify("map the auth directory")
        assert result.category == "mapper_command"

    def test_existing_status_query_no_regression(
        self, classifier: IntentClassifier
    ) -> None:
        """'what's the status' still classifies as status_query (no regression)."""
        result = classifier.classify("what's the status")
        assert result.category == "status_query"
