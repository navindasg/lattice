# Agent Procedures

## Approval Rules
- File deletions: always require human approval
- Git push: always require human approval
- New file creation: auto-approve in project directory
- File edits: auto-approve for assigned tasks

## Work Assignment
- Assign tasks based on instance availability and project affinity
- Prefer re-using an instance already working in the same directory
- Maximum 3 concurrent instances by default

## Escalation
- Escalate to human when: circuit breaker trips, conflicting edits detected,
  test failures after 2 retries, security-sensitive operations
