# Ticket 03: Tasks Module

## File to implement
`src/miniapi/tasks.py`

## Tests to pass
`tests/test_tasks.py` (5 tests)

## Spec

Implement CRUD endpoints for task management with status transitions on the `router` APIRouter already defined in the stub.

### Data model
```python
{
    "id": str,            # UUID4, generated server-side
    "title": str,         # required, non-empty
    "status": str,        # "todo" | "doing" | "done", default "todo"
    "project_id": str,    # required, must reference an existing project
    "assignee_id": str | None,  # optional, must reference existing user if set
    "tag_ids": list[str]  # default empty list
}
```

### Endpoints

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| POST | `/` | Create task | 201 |
| GET | `/` | List all tasks | 200 |
| GET | `/{task_id}` | Get single task | 200 or 404 |
| PATCH | `/{task_id}` | Update task fields | 200 or 404 |
| DELETE | `/{task_id}` | Delete task | 204 or 404 |

### Rules
- Use `miniapi.store.store.tasks` dict for persistence (key = id)
- Validate `project_id` exists in `store.projects` — return 400 `{"detail": "project not found"}` if not
- Validate `assignee_id` exists in `store.users` if provided — return 400 `{"detail": "assignee not found"}` if not
- POST body: `{"title": "...", "project_id": "...", "assignee_id": "..." | null}`
- POST sets `status` to `"todo"` and `tag_ids` to `[]` by default
- PATCH body: any subset of `{"title", "status", "assignee_id"}`
- PATCH `status` must be one of `"todo"`, `"doing"`, `"done"` — return 422 otherwise
- GET `/` returns a JSON list of all task dicts
- GET `/{task_id}` returns the task dict or 404 `{"detail": "not found"}`
- DELETE returns 204 or 404

### Constraints
- Do NOT modify `app.py` or `store.py`
- Do NOT add new dependencies
- Do NOT create additional files
- Use Pydantic BaseModel for request validation
