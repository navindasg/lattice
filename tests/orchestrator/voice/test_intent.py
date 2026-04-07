"""Tests for lattice.orchestrator.voice.intent — IntentClassifier.

Covers:
    - All original intent categories (status_query, mapper_command, etc.)
    - All CC instance control intents (cc_command, cc_approve, cc_deny, etc.)
    - Slot extraction (instance number, message)
    - Instance number validation (1-9 valid, 0 and 10+ rejected)
    - Priority: CC intents take precedence when instance number is present
    - orchestrator_freeform fallback for unrecognized input
    - 120+ fixture-based parametrized tests
"""
import pytest

from lattice.orchestrator.voice.intent import IntentClassifier
from lattice.orchestrator.voice.models import IntentResult
from tests.orchestrator.voice.fixtures.transcripts import TRANSCRIPT_FIXTURES


@pytest.fixture()
def classifier() -> IntentClassifier:
    return IntentClassifier()


# ---------------------------------------------------------------------------
# Original intent category tests (no regressions)
# ---------------------------------------------------------------------------


class TestOriginalIntentCategories:
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

    def test_recognized_has_nonzero_confidence(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("what's the status")
        assert result.confidence > 0.0

    def test_transcript_preserved_in_result(self, classifier: IntentClassifier) -> None:
        utterance = "map the auth directory"
        result = classifier.classify(utterance)
        assert result.transcript == utterance


class TestOriginalSlotExtraction:
    def test_mapper_command_extracts_target(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("map the auth directory")
        assert "target" in result.extracted
        assert result.extracted["target"] != ""

    def test_mapper_command_no_target_has_empty_extracted(
        self, classifier: IntentClassifier
    ) -> None:
        result = classifier.classify("map status")
        assert result.category == "mapper_command"


# ---------------------------------------------------------------------------
# Fixture-based parametrized tests (all categories)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# CC command intent tests
# ---------------------------------------------------------------------------


class TestCCCommandIntent:
    def test_tell_number_to_message(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("tell 3 to refactor the auth module")
        assert result.category == "cc_command"
        assert result.extracted["instance"] == "3"
        assert result.extracted["message"] == "refactor the auth module"

    def test_tell_instance_number_to_message(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("tell instance 3 to refactor the auth module")
        assert result.category == "cc_command"
        assert result.extracted["instance"] == "3"
        assert result.extracted["message"] == "refactor the auth module"

    def test_bare_number_message(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("3 refactor the auth module")
        assert result.category == "cc_command"
        assert result.extracted["instance"] == "3"
        assert result.extracted["message"] == "refactor the auth module"

    def test_tell_without_to(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("tell 1 fix the auth bug")
        assert result.category == "cc_command"
        assert result.extracted["instance"] == "1"
        assert "fix the auth bug" in result.extracted["message"]

    def test_all_valid_instance_numbers(self, classifier: IntentClassifier) -> None:
        for n in range(1, 10):
            result = classifier.classify(f"tell {n} to do something")
            assert result.category == "cc_command", f"Failed for instance {n}"
            assert result.extracted["instance"] == str(n)


# ---------------------------------------------------------------------------
# CC approve intent tests
# ---------------------------------------------------------------------------


class TestCCApproveIntent:
    def test_number_approved(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("4 approved")
        assert result.category == "cc_approve"
        assert result.extracted["instance"] == "4"

    def test_approve_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("approve 4")
        assert result.category == "cc_approve"
        assert result.extracted["instance"] == "4"

    def test_number_yes(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("4 yes")
        assert result.category == "cc_approve"
        assert result.extracted["instance"] == "4"

    def test_yes_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("yes 3")
        assert result.category == "cc_approve"
        assert result.extracted["instance"] == "3"

    def test_go_ahead_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("go ahead 5")
        assert result.category == "cc_approve"
        assert result.extracted["instance"] == "5"

    def test_proceed_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("proceed 6")
        assert result.category == "cc_approve"
        assert result.extracted["instance"] == "6"


# ---------------------------------------------------------------------------
# CC deny intent tests
# ---------------------------------------------------------------------------


class TestCCDenyIntent:
    def test_number_denied(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("4 denied")
        assert result.category == "cc_deny"
        assert result.extracted["instance"] == "4"

    def test_deny_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("deny 4")
        assert result.category == "cc_deny"
        assert result.extracted["instance"] == "4"

    def test_number_no(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("4 no")
        assert result.category == "cc_deny"
        assert result.extracted["instance"] == "4"

    def test_no_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("no 3")
        assert result.category == "cc_deny"
        assert result.extracted["instance"] == "3"

    def test_reject_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("reject 5")
        assert result.category == "cc_deny"
        assert result.extracted["instance"] == "5"

    def test_number_rejected(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("5 rejected")
        assert result.category == "cc_deny"
        assert result.extracted["instance"] == "5"


# ---------------------------------------------------------------------------
# CC deny_redirect intent tests
# ---------------------------------------------------------------------------


class TestCCDenyRedirectIntent:
    def test_number_denied_with_redirect(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("6 denied, tell it to use AWS instead")
        assert result.category == "cc_deny_redirect"
        assert result.extracted["instance"] == "6"
        assert "use AWS instead" in result.extracted["message"]

    def test_deny_number_with_redirect(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("deny 3, use PostgreSQL")
        assert result.category == "cc_deny_redirect"
        assert result.extracted["instance"] == "3"
        assert "PostgreSQL" in result.extracted["message"]

    def test_number_no_with_tell_redirect(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("4 no, tell it to try a different approach")
        assert result.category == "cc_deny_redirect"
        assert result.extracted["instance"] == "4"
        assert "try a different approach" in result.extracted["message"]

    def test_deny_redirect_precedes_deny(self, classifier: IntentClassifier) -> None:
        """Deny with redirect should classify as cc_deny_redirect, not cc_deny."""
        result = classifier.classify("7 denied, use Redis instead of Memcached")
        assert result.category == "cc_deny_redirect"
        assert result.extracted["instance"] == "7"


# ---------------------------------------------------------------------------
# CC status intent tests
# ---------------------------------------------------------------------------


class TestCCStatusIntent:
    def test_whats_number_doing(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("what's 2 doing")
        assert result.category == "cc_status"
        assert result.extracted["instance"] == "2"

    def test_status_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("status 2")
        assert result.category == "cc_status"
        assert result.extracted["instance"] == "2"

    def test_hows_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("how's 2")
        assert result.category == "cc_status"
        assert result.extracted["instance"] == "2"

    def test_how_is_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("how is 3")
        assert result.category == "cc_status"
        assert result.extracted["instance"] == "3"

    def test_number_status(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("5 status")
        assert result.category == "cc_status"
        assert result.extracted["instance"] == "5"


# ---------------------------------------------------------------------------
# CC interrupt intent tests
# ---------------------------------------------------------------------------


class TestCCInterruptIntent:
    def test_stop_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("stop 5")
        assert result.category == "cc_interrupt"
        assert result.extracted["instance"] == "5"

    def test_interrupt_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("interrupt 5")
        assert result.category == "cc_interrupt"
        assert result.extracted["instance"] == "5"

    def test_kill_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("kill 7")
        assert result.category == "cc_interrupt"
        assert result.extracted["instance"] == "7"

    def test_cancel_number(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("cancel 2")
        assert result.category == "cc_interrupt"
        assert result.extracted["instance"] == "2"

    def test_number_stop(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("3 stop")
        assert result.category == "cc_interrupt"
        assert result.extracted["instance"] == "3"


# ---------------------------------------------------------------------------
# orchestrator_freeform intent tests
# ---------------------------------------------------------------------------


class TestOrchestratorFreeformIntent:
    def test_unrecognized_becomes_freeform(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("we need to ship auth by Friday")
        assert result.category == "orchestrator_freeform"

    def test_freeform_has_message(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("hello testing one two three")
        assert result.category == "orchestrator_freeform"
        assert result.extracted["message"] == "hello testing one two three"

    def test_freeform_has_moderate_confidence(self, classifier: IntentClassifier) -> None:
        result = classifier.classify("the client wants dark mode")
        assert result.confidence == 0.5

    def test_gibberish_is_freeform(self, classifier: IntentClassifier) -> None:
        """Pure gibberish now goes to orchestrator, not 'unrecognized'."""
        result = classifier.classify("asdfghjkl xyz qwerty")
        assert result.category == "orchestrator_freeform"


# ---------------------------------------------------------------------------
# Instance number validation (1-9 valid, 0 and 10+ rejected)
# ---------------------------------------------------------------------------


class TestInstanceNumberValidation:
    def test_instance_0_not_accepted(self, classifier: IntentClassifier) -> None:
        """Instance 0 should NOT be accepted — falls through to other rules."""
        result = classifier.classify("approve 0")
        assert result.category != "cc_approve"

    def test_instance_10_not_accepted(self, classifier: IntentClassifier) -> None:
        """Instance 10+ should NOT be accepted — falls through to other rules."""
        result = classifier.classify("approve 10")
        assert result.category != "cc_approve"

    def test_instance_99_not_accepted(self, classifier: IntentClassifier) -> None:
        """Large instance numbers should NOT be accepted."""
        result = classifier.classify("status 99")
        # Falls through to general status_query (has "status" keyword)
        assert result.category != "cc_status"

    def test_instance_0_command_falls_through(self, classifier: IntentClassifier) -> None:
        """'tell 0 to ...' with invalid instance falls to context_injection."""
        result = classifier.classify("tell 0 to do something")
        # "tell" keyword matches context_injection
        assert result.category == "context_injection"


# ---------------------------------------------------------------------------
# Priority: CC intents over existing intents
# ---------------------------------------------------------------------------


class TestCCIntentPriority:
    def test_status_number_is_cc_not_general(self, classifier: IntentClassifier) -> None:
        """'status 3' should be cc_status, not status_query."""
        result = classifier.classify("status 3")
        assert result.category == "cc_status"
        assert result.extracted["instance"] == "3"

    def test_stop_number_is_interrupt_not_task(self, classifier: IntentClassifier) -> None:
        """'stop 5' should be cc_interrupt, not task_dispatch or other."""
        result = classifier.classify("stop 5")
        assert result.category == "cc_interrupt"

    def test_general_status_still_works(self, classifier: IntentClassifier) -> None:
        """'what's the status' (no number) still classifies as status_query."""
        result = classifier.classify("what's the status")
        assert result.category == "status_query"

    def test_map_command_still_works(self, classifier: IntentClassifier) -> None:
        """'map the auth directory' still classifies as mapper_command."""
        result = classifier.classify("map the auth directory")
        assert result.category == "mapper_command"

    def test_task_dispatch_still_works(self, classifier: IntentClassifier) -> None:
        """'fix the memory leak' still classifies as task_dispatch."""
        result = classifier.classify("fix the memory leak")
        assert result.category == "task_dispatch"

    def test_context_injection_still_works(self, classifier: IntentClassifier) -> None:
        """'add context about the deadline' still classifies as context_injection."""
        result = classifier.classify("add context about the deadline")
        assert result.category == "context_injection"

    def test_external_fetch_still_works(self, classifier: IntentClassifier) -> None:
        """'look up SAML spec' still classifies as external_fetch."""
        result = classifier.classify("look up SAML spec")
        assert result.category == "external_fetch"


# ---------------------------------------------------------------------------
# External fetch (no regression)
# ---------------------------------------------------------------------------


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
        result = classifier.classify("map the auth directory")
        assert result.category == "mapper_command"

    def test_existing_status_query_no_regression(
        self, classifier: IntentClassifier
    ) -> None:
        result = classifier.classify("what's the status")
        assert result.category == "status_query"
