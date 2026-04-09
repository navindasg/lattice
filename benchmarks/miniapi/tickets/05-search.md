# Ticket 05: Search Module

## File to implement
`src/miniapi/search.py`

## Tests to pass
`tests/test_search.py` (3 tests)

## Spec

Implement task filtering/search on the `router` APIRouter already defined in the stub.

### Endpoints

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| GET | `/tasks` | Search tasks with filters | 200 |

### Query parameters (all optional, combinable)

| Param | Type | Filter logic |
|-------|------|-------------|
| `status` | str | Exact match on `task["status"]` |
| `assignee_id` | str | Exact match on `task["assignee_id"]` |
| `project_id` | str | Exact match on `task["project_id"]` |
| `tag` | str | Tag ID must be in `task["tag_ids"]` |
| `q` | str | Case-insensitive substring match on `task["title"]` |

### Rules
- Read from `miniapi.store.store.tasks`
- All filters are AND-combined (task must match ALL provided filters)
- If no filters provided, return all tasks
- Return a JSON list of matching task dicts
- No pagination needed — return full result set

### Examples
```
GET /search/tasks?status=doing
GET /search/tasks?assignee_id=abc-123&status=todo
GET /search/tasks?tag=tag-456
GET /search/tasks?q=deploy
GET /search/tasks?status=done&q=fix
```

### Constraints
- Do NOT modify `app.py` or `store.py`
- Do NOT add new dependencies
- Do NOT create additional files
- Use FastAPI Query parameters (not Pydantic body)
