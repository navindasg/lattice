"""Intent classifier for voice commands using ordered regex rules.

Rules are evaluated in priority order. The first matching rule wins.
Priority order is important: mapper_command is checked before status_query
to handle utterances like "map status" correctly (map verb takes precedence).
external_fetch is placed after context_injection and before status_query.

IntentCategory values:
    task_dispatch     — "start working on X", "fix X", "implement X"
    context_injection — "tell instance N about X", "add context X"
    mapper_command    — "map X", "document X", "analyze X"
    external_fetch    — "look up X", "search for X", "check the github issues"
    status_query      — "what's the status", "show me progress"
    unrecognized      — no pattern matched
"""
from __future__ import annotations

import re

from lattice.orchestrator.voice.models import IntentCategory, IntentResult

# ---------------------------------------------------------------------------
# Compiled patterns with optional slot extractors
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
    """Classifies transcribed utterances into one of 5 intent categories.

    Uses ordered regex rules evaluated left-to-right. First match wins.
    Returns IntentResult with category, original transcript, confidence,
    and optionally extracted slot values (e.g. target path for mapper commands).
    """

    def classify(self, transcript: str) -> IntentResult:
        """Classify a transcript into an IntentResult.

        Args:
            transcript: The transcribed utterance to classify.

        Returns:
            IntentResult with category "unrecognized" if no rule matches.
        """
        for pattern, category, slot_name in _INTENT_RULES:
            match = pattern.search(transcript)
            if match:
                extracted: dict[str, str] = {}
                if slot_name is not None:
                    try:
                        raw_value = match.group(1)
                        if raw_value:
                            extracted[slot_name] = raw_value.strip()
                    except IndexError:
                        pass

                return IntentResult(
                    category=category,
                    transcript=transcript,
                    confidence=0.9,
                    extracted=extracted,
                )

        return IntentResult(
            category="unrecognized",
            transcript=transcript,
            confidence=0.0,
            extracted={},
        )
