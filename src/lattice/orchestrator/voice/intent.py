"""Intent classifier for voice commands using ordered regex rules.

Rules are evaluated in priority order. The first matching rule wins.

CC instance control intents are checked first when instance numbers (1-9)
are present, so "status 3" becomes cc_status (not status_query) and
"tell 3 to fix the bug" becomes cc_command (not context_injection).

Priority order:
    cc_deny_redirect  — "6 denied, tell it to use AWS" (must precede cc_deny)
    cc_approve        — "4 approved", "approve 4", "4 yes"
    cc_deny           — "4 denied", "deny 4", "4 no"
    cc_command        — "tell 3 to ...", "3 refactor the auth module"
    cc_status         — "status 3", "what's 2 doing", "how's 2"
    cc_interrupt      — "stop 5", "interrupt 5"
    mapper_command    — "map X", "document X" (checked before status_query)
    task_dispatch     — "start working on X", "fix X"
    context_injection — "add context X" (no instance number)
    external_fetch    — "look up X", "search for X"
    status_query      — "what's the status" (no instance number)
    orchestrator_freeform — anything unrecognized goes to orchestrator

IntentCategory values:
    cc_command            — "tell 3 to X", "3 do X"
    cc_approve            — "4 approved", "approve 4", "4 yes"
    cc_deny               — "4 denied", "deny 4", "4 no"
    cc_deny_redirect      — "6 denied, tell it to use AWS"
    cc_status             — "status 3", "what's 2 doing"
    cc_interrupt          — "stop 5", "interrupt 5"
    orchestrator_freeform — anything not matched (sent to orchestrator LLM)
    task_dispatch         — "start working on X", "fix X", "implement X"
    context_injection     — "add context X"
    mapper_command        — "map X", "document X", "analyze X"
    external_fetch        — "look up X", "search for X"
    status_query          — "what's the status", "show me progress"
    unrecognized          — legacy: no pattern matched (replaced by orchestrator_freeform)
"""
from __future__ import annotations

import re

from lattice.orchestrator.voice.models import IntentCategory, IntentResult

# ---------------------------------------------------------------------------
# Valid CC instance number range (1-9)
# ---------------------------------------------------------------------------

_VALID_INSTANCE_RANGE = range(1, 10)

# ---------------------------------------------------------------------------
# CC instance control patterns — checked FIRST (highest priority)
# ---------------------------------------------------------------------------
# These use a two-pass strategy: the regex match extracts slots,
# then a validator confirms the instance number is in range 1-9.
# If out of range, the rule is skipped and the next rule is tried.

# Each entry: (compiled_regex, IntentCategory, slot_extractors)
# slot_extractors is a dict mapping group_number -> slot_name.

_CC_INTENT_RULES: list[
    tuple[re.Pattern[str], IntentCategory, dict[int, str]]
] = [
    # cc_deny_redirect: "6 denied, tell it to use AWS" / "deny 6, use AWS instead"
    # Must precede cc_deny to capture the redirect message.
    (
        re.compile(
            r"(?:^(\d+)\s+(?:denied|deny|no)\s*[,;:]\s*(?:tell\s+(?:it|them)\s+(?:to\s+)?)?(.+)$"
            r"|^deny\s+(\d+)\s*[,;:]\s*(?:tell\s+(?:it|them)\s+(?:to\s+)?)?(.+)$)",
            re.IGNORECASE,
        ),
        "cc_deny_redirect",
        {},  # custom extraction in classifier
    ),
    # cc_approve: "4 approved", "approve 4", "4 yes", "yes 4"
    (
        re.compile(
            r"(?:^(\d+)\s+(?:approved?|yes|y|go\s+ahead|proceed)$"
            r"|^(?:approved?|yes|y|go\s+ahead|proceed)\s+(\d+)$)",
            re.IGNORECASE,
        ),
        "cc_approve",
        {},  # custom extraction
    ),
    # cc_deny: "4 denied", "deny 4", "4 no", "no 4"
    # Note: "stop" is NOT here — "stop N" is cc_interrupt.
    # Note: "deny|denied" not "denied?" — "denied?" matches "denie"/"denied" not "deny".
    (
        re.compile(
            r"(?:^(\d+)\s+(?:deny|denied|no|n|reject(?:ed)?)$"
            r"|^(?:deny|denied|no|n|reject(?:ed)?)\s+(\d+)$)",
            re.IGNORECASE,
        ),
        "cc_deny",
        {},  # custom extraction
    ),
    # cc_status: "status 3", "what's 2 doing", "how's 2", "how is 3"
    # Checked BEFORE cc_command so "5 status" matches cc_status, not cc_command.
    (
        re.compile(
            r"(?:^(?:status|what(?:'|')?s|how(?:'|')?s|how\s+is)\s+(\d+)(?:\s+doing)?$"
            r"|^(\d+)\s+status$)",
            re.IGNORECASE,
        ),
        "cc_status",
        {},  # custom extraction
    ),
    # cc_interrupt: "stop 5", "interrupt 5", "kill 5", "cancel 5"
    # Checked BEFORE cc_command so "5 stop" matches cc_interrupt, not cc_command.
    (
        re.compile(
            r"(?:^(?:stop|interrupt|kill|cancel)\s+(\d+)$"
            r"|^(\d+)\s+(?:stop|interrupt|kill|cancel)$)",
            re.IGNORECASE,
        ),
        "cc_interrupt",
        {},  # custom extraction
    ),
    # cc_command: "tell 3 to ...", "tell instance 3 to ...", "3 refactor the auth"
    # This is the most greedy pattern (^N .+$), so it MUST come LAST among CC rules.
    (
        re.compile(
            r"(?:^tell\s+(?:instance\s+)?(\d+)\s+(?:to\s+)?(.+)$"
            r"|^(\d+)\s+(.+)$)",
            re.IGNORECASE,
        ),
        "cc_command",
        {},  # custom extraction
    ),
]


def _extract_cc_slots(
    category: IntentCategory,
    match: re.Match[str],
) -> dict[str, str] | None:
    """Extract instance number and optional message from a CC intent match.

    Returns None if the instance number is outside the valid range (1-9),
    signaling the caller to skip this rule and try the next.

    Args:
        category: The CC intent category matched.
        match: The regex match object.

    Returns:
        Extracted slots dict with 'instance' and optionally 'message',
        or None if instance number is invalid.
    """
    groups = match.groups()

    if category == "cc_deny_redirect":
        # Pattern has 4 groups: (prefix_num, prefix_msg, suffix_num, suffix_msg)
        if groups[0] is not None:
            instance_str, message = groups[0], groups[1]
        else:
            instance_str, message = groups[2], groups[3]
        instance = int(instance_str)
        if instance not in _VALID_INSTANCE_RANGE:
            return None
        return {"instance": str(instance), "message": message.strip()}

    if category == "cc_approve":
        instance_str = groups[0] or groups[1]
        instance = int(instance_str)
        if instance not in _VALID_INSTANCE_RANGE:
            return None
        return {"instance": str(instance)}

    if category == "cc_deny":
        instance_str = groups[0] or groups[1]
        instance = int(instance_str)
        if instance not in _VALID_INSTANCE_RANGE:
            return None
        return {"instance": str(instance)}

    if category == "cc_command":
        # Pattern: (tell_num, tell_msg, bare_num, bare_msg)
        if groups[0] is not None:
            instance_str, message = groups[0], groups[1]
        else:
            instance_str, message = groups[2], groups[3]
        instance = int(instance_str)
        if instance not in _VALID_INSTANCE_RANGE:
            return None
        return {"instance": str(instance), "message": message.strip()}

    if category == "cc_status":
        instance_str = groups[0] or groups[1]
        instance = int(instance_str)
        if instance not in _VALID_INSTANCE_RANGE:
            return None
        return {"instance": str(instance)}

    if category == "cc_interrupt":
        instance_str = groups[0] or groups[1]
        instance = int(instance_str)
        if instance not in _VALID_INSTANCE_RANGE:
            return None
        return {"instance": str(instance)}

    return None


# ---------------------------------------------------------------------------
# Existing intent patterns (lower priority than CC intents)
# ---------------------------------------------------------------------------
# Each entry: (compiled_regex, IntentCategory, slot_name | None)
# The optional slot_name extracts group(1) from the match into extracted dict.

_INTENT_RULES: list[tuple[re.Pattern[str], IntentCategory, str | None]] = [
    # mapper_command: check before status_query to handle "map status" correctly
    (
        re.compile(
            r"\b(?:map|document|analyze|re-?map|mapper)\b"
            r"(?:\s+(?:the\s+)?(?:init\s+on\s+)?(.+?))?$",
            re.IGNORECASE,
        ),
        "mapper_command",
        "target",
    ),
    # task_dispatch: start/work on/begin/implement/fix something
    (
        re.compile(
            r"\b(?:start|work\s+on|begin|implement|fix)\b",
            re.IGNORECASE,
        ),
        "task_dispatch",
        None,
    ),
    # context_injection: add/inject/tell/inform/note/context
    (
        re.compile(
            r"\b(?:add|inject|tell|inform|context|note)\b",
            re.IGNORECASE,
        ),
        "context_injection",
        None,
    ),
    # external_fetch: look up / search for / fetch / get me / check github/mattermost/issues/prs/status
    (
        re.compile(
            r"\b(?:"
            r"look\s+up|search\s+for|"
            r"fetch(?:\s+(?:the|a|latest|all|some|new))*|"
            r"get\s+me(?:\s+(?:the|a|latest|all|some|new))*|"
            r"find(?:\s+(?:the|a))?|"
            r"check(?:\s+the)?(?:\s+(?:github|mattermost|issues?|prs?|ci\s+status|pull\s+requests?))"
            r")\b"
            r"(?:\s+(.+?))?$",
            re.IGNORECASE,
        ),
        "external_fetch",
        "query",
    ),
    # status_query: what/how/show/status/progress/running/failures/utilization/instances
    (
        re.compile(
            r"\b(?:status|what(?:'|')?s|what\s+are|how(?:'|')?s|how\s+is|show|"
            r"progress|running|failures?|utilization|update|instances?)\b",
            re.IGNORECASE,
        ),
        "status_query",
        None,
    ),
]


class IntentClassifier:
    """Classifies transcribed utterances into intent categories.

    Uses ordered regex rules evaluated left-to-right. First match wins.
    CC instance control intents (cc_command, cc_approve, etc.) are checked
    first and take priority when an instance number (1-9) is present.

    Returns IntentResult with category, original transcript, confidence,
    and optionally extracted slot values (e.g. instance number, message).
    """

    def classify(self, transcript: str) -> IntentResult:
        """Classify a transcript into an IntentResult.

        CC instance intents are checked first. If no CC pattern matches
        (or instance number is out of range 1-9), falls through to
        general intents. Unrecognized transcripts are classified as
        orchestrator_freeform (routed to orchestrator LLM for interpretation).

        Args:
            transcript: The transcribed utterance to classify.

        Returns:
            IntentResult with the matched category and extracted slots.
        """
        stripped = transcript.strip()

        # Phase 1: Try CC instance control patterns (highest priority)
        for pattern, category, _slot_spec in _CC_INTENT_RULES:
            match = pattern.match(stripped)
            if match:
                extracted = _extract_cc_slots(category, match)
                if extracted is not None:
                    return IntentResult(
                        category=category,
                        transcript=transcript,
                        confidence=0.9,
                        extracted=extracted,
                    )
                # Instance number out of range — skip this CC rule,
                # fall through to try other CC rules or general rules.

        # Phase 2: Try general intent patterns (lower priority)
        for pattern, category, slot_name in _INTENT_RULES:
            match = pattern.search(transcript)
            if match:
                extracted_general: dict[str, str] = {}
                if slot_name is not None:
                    try:
                        raw_value = match.group(1)
                        if raw_value:
                            extracted_general[slot_name] = raw_value.strip()
                    except IndexError:
                        pass

                return IntentResult(
                    category=category,
                    transcript=transcript,
                    confidence=0.9,
                    extracted=extracted_general,
                )

        # Phase 3: Unrecognized → orchestrator_freeform
        # Instead of returning "unrecognized", route to the orchestrator
        # agent for LLM-powered interpretation.
        return IntentResult(
            category="orchestrator_freeform",
            transcript=transcript,
            confidence=0.5,
            extracted={"message": transcript},
        )
