# Ticket 06: Stats Module

## File to implement
`src/miniapi/stats.py`

## Tests to pass
`tests/test_stats.py` (3 tests)

## Spec

Implement a dashboard statistics endpoint on the `router` APIRouter already defined in the stub.

### Endpoints

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| GET | `/` | Get project statistics | 200 |

### Response format
```json
{
    "total_users": 3,
    "total_projects": 2,
    "total_tasks": 10,
    "tasks_by_status": {
        "todo": 4,
        "doing": 3,
        "done": 3
    },
    "completion_pct": 30.0,
    "tasks_per_user": {
        "user-id-1": 4,
        "user-id-2": 6
    }
}
```

### Rules
- Read from `miniapi.store.store` (users, projects, tasks)
- `total_users`: count of `store.users`
- `total_projects`: count of `store.projects`
- `total_tasks`: count of `store.tasks`
- `tasks_by_status`: count tasks grouped by `status` field. Include all three keys (`todo`, `doing`, `done`) even if count is 0
- `completion_pct`: `(done_count / total_tasks) * 100`, rounded to 1 decimal. Return `0.0` if no tasks
- `tasks_per_user`: count tasks grouped by `assignee_id`. Exclude tasks where `assignee_id` is `None`
- All values computed live from the store (no caching)

### Constraints
- Do NOT modify `app.py` or `store.py`
- Do NOT add new dependencies
- Do NOT create additional files
