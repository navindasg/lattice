"""Fixture transcript set for intent classifier tests.

Each entry is a tuple of (utterance, expected_category).
At least 10 entries per intent category.

CC instance control intents (cc_command, cc_approve, cc_deny, cc_deny_redirect,
cc_status, cc_interrupt) and orchestrator_freeform are included for Wave 2.
"""

TRANSCRIPT_FIXTURES: list[tuple[str, str]] = [
    # -----------------------------------------------------------------------
    # cc_command (12 entries)
    # -----------------------------------------------------------------------
    ("tell 3 to refactor the auth module", "cc_command"),
    ("tell instance 3 to refactor the auth module", "cc_command"),
    ("3 refactor the auth module", "cc_command"),
    ("tell 1 to fix the login bug", "cc_command"),
    ("tell instance 7 to add unit tests", "cc_command"),
    ("5 implement the search feature", "cc_command"),
    ("tell 2 to update the README", "cc_command"),
    ("9 run the test suite", "cc_command"),
    ("tell 4 to clean up imports", "cc_command"),
    ("tell instance 6 to deploy to staging", "cc_command"),
    ("8 check the database migrations", "cc_command"),
    ("tell 1 fix the auth bug", "cc_command"),
    # -----------------------------------------------------------------------
    # cc_approve (10 entries)
    # -----------------------------------------------------------------------
    ("4 approved", "cc_approve"),
    ("approve 4", "cc_approve"),
    ("4 yes", "cc_approve"),
    ("yes 3", "cc_approve"),
    ("1 approved", "cc_approve"),
    ("approve 9", "cc_approve"),
    ("7 yes", "cc_approve"),
    ("2 go ahead", "cc_approve"),
    ("go ahead 5", "cc_approve"),
    ("proceed 6", "cc_approve"),
    # -----------------------------------------------------------------------
    # cc_deny (10 entries)
    # -----------------------------------------------------------------------
    ("4 denied", "cc_deny"),
    ("deny 4", "cc_deny"),
    ("4 no", "cc_deny"),
    ("no 3", "cc_deny"),
    ("1 denied", "cc_deny"),
    ("deny 9", "cc_deny"),
    ("7 no", "cc_deny"),
    ("reject 5", "cc_deny"),
    ("5 rejected", "cc_deny"),
    ("stop 2", "cc_interrupt"),  # "stop N" is cc_interrupt, not cc_deny
    # -----------------------------------------------------------------------
    # cc_deny_redirect (10 entries)
    # -----------------------------------------------------------------------
    ("6 denied, tell it to use AWS instead", "cc_deny_redirect"),
    ("deny 3, use PostgreSQL", "cc_deny_redirect"),
    ("4 no, tell it to try a different approach", "cc_deny_redirect"),
    ("deny 1, tell them to use the cache layer", "cc_deny_redirect"),
    ("7 denied, use Redis instead of Memcached", "cc_deny_redirect"),
    ("deny 2, switch to async implementation", "cc_deny_redirect"),
    ("5 no, tell it to use the existing API", "cc_deny_redirect"),
    ("deny 8, tell it to refactor first", "cc_deny_redirect"),
    ("9 denied, tell them to add error handling", "cc_deny_redirect"),
    ("deny 6, use TypeScript instead", "cc_deny_redirect"),
    # -----------------------------------------------------------------------
    # cc_status (10 entries)
    # -----------------------------------------------------------------------
    ("what's 2 doing", "cc_status"),
    ("status 2", "cc_status"),
    ("how's 2", "cc_status"),
    ("what's 5 doing", "cc_status"),
    ("status 1", "cc_status"),
    ("how's 7", "cc_status"),
    ("how is 3", "cc_status"),
    ("status 9", "cc_status"),
    ("what's 4 doing", "cc_status"),
    ("how is 6", "cc_status"),
    # -----------------------------------------------------------------------
    # cc_interrupt (10 entries)
    # -----------------------------------------------------------------------
    ("stop 5", "cc_interrupt"),
    ("interrupt 5", "cc_interrupt"),
    ("stop 3", "cc_interrupt"),
    ("interrupt 1", "cc_interrupt"),
    ("kill 7", "cc_interrupt"),
    ("cancel 2", "cc_interrupt"),
    ("stop 9", "cc_interrupt"),
    ("interrupt 4", "cc_interrupt"),
    ("kill 6", "cc_interrupt"),
    ("cancel 8", "cc_interrupt"),
    # -----------------------------------------------------------------------
    # orchestrator_freeform (8 entries)
    # -----------------------------------------------------------------------
    ("we need to ship auth by Friday", "orchestrator_freeform"),
    ("hello testing one two three", "orchestrator_freeform"),
    ("the client wants dark mode", "orchestrator_freeform"),
    ("let's prioritize the payment integration", "orchestrator_freeform"),
    ("can we get a code review before EOD", "orchestrator_freeform"),
    ("push everything to staging", "orchestrator_freeform"),
    ("the database needs a backup plan", "orchestrator_freeform"),
    ("we should refactor before adding more features", "orchestrator_freeform"),
    # -----------------------------------------------------------------------
    # status_query (11 entries) — general status, no instance number
    # -----------------------------------------------------------------------
    ("what's the status", "status_query"),
    ("how's instance two doing", "status_query"),
    ("show me the progress", "status_query"),
    ("status report", "status_query"),
    ("what are the instances doing", "status_query"),
    ("how is the auth task going", "status_query"),
    ("show status", "status_query"),
    ("what's running", "status_query"),
    ("are there any failures", "status_query"),
    ("give me a status update", "status_query"),
    ("what's the utilization", "status_query"),
    # -----------------------------------------------------------------------
    # mapper_command (11 entries)
    # -----------------------------------------------------------------------
    ("map the auth directory", "mapper_command"),
    ("document the API folder", "mapper_command"),
    ("analyze src utils", "mapper_command"),
    ("map init on the project", "mapper_command"),
    ("run mapper on tests", "mapper_command"),
    ("document the models", "mapper_command"),
    ("map status", "mapper_command"),
    ("re-map the auth module", "mapper_command"),
    ("analyze the database layer", "mapper_command"),
    ("map the entire project", "mapper_command"),
    ("document src/api", "mapper_command"),
    # -----------------------------------------------------------------------
    # task_dispatch (11 entries)
    # -----------------------------------------------------------------------
    ("start working on the auth refactor", "task_dispatch"),
    ("work on ticket 42", "task_dispatch"),
    ("begin implementing the login page", "task_dispatch"),
    ("start the database migration", "task_dispatch"),
    ("fix the broken tests", "task_dispatch"),
    ("implement SAML support", "task_dispatch"),
    ("start task number three", "task_dispatch"),
    ("work on the API endpoint", "task_dispatch"),
    ("begin the refactoring", "task_dispatch"),
    ("fix the memory leak", "task_dispatch"),
    ("implement the new feature", "task_dispatch"),
    # -----------------------------------------------------------------------
    # context_injection (11 entries)
    # -----------------------------------------------------------------------
    ("tell instance one the user wants SAML", "context_injection"),
    ("add context about the deadline", "context_injection"),
    ("inject the new requirements", "context_injection"),
    ("note that we're using PostgreSQL now", "context_injection"),
    ("inform the workers about the API change", "context_injection"),
    ("add this to the project context", "context_injection"),
    ("tell them about the schema change", "context_injection"),
    ("inject the test results", "context_injection"),
    ("note the user feedback", "context_injection"),
    ("add the security requirements", "context_injection"),
    ("tell instance two about the bug", "context_injection"),
    # -----------------------------------------------------------------------
    # external_fetch (8 entries)
    # -----------------------------------------------------------------------
    ("look up SAML spec", "external_fetch"),
    ("search for python async patterns", "external_fetch"),
    ("fetch the latest issues", "external_fetch"),
    ("check the github status", "external_fetch"),
    ("check the mattermost channel", "external_fetch"),
    ("get me the CI status", "external_fetch"),
    ("look up asyncio best practices", "external_fetch"),
    ("search for DuckDB documentation", "external_fetch"),
]
