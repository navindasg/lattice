"""Fixture transcript set for intent classifier tests.

Each entry is a tuple of (utterance, expected_category).
At least 10 entries per intent category (task_dispatch, context_injection,
status_query, mapper_command).
"""

TRANSCRIPT_FIXTURES: list[tuple[str, str]] = [
    # status_query (11 entries)
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
    # mapper_command (11 entries)
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
    # task_dispatch (11 entries)
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
    # context_injection (11 entries)
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
    # external_fetch (8 entries)
    ("look up SAML spec", "external_fetch"),
    ("search for python async patterns", "external_fetch"),
    ("fetch the latest issues", "external_fetch"),
    ("check the github status", "external_fetch"),
    ("check the mattermost channel", "external_fetch"),
    ("get me the CI status", "external_fetch"),
    ("look up asyncio best practices", "external_fetch"),
    ("search for DuckDB documentation", "external_fetch"),
]
