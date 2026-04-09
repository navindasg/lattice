# Ticket 04: Tags Module

## File to implement
`src/miniapi/tags.py`

## Tests to pass
`tests/test_tags.py` (4 tests)

## Spec

Implement tag management and task-tagging endpoints on the `router` APIRouter already defined in the stub.

### Data model
```python
{
    "id": str,    # UUID4, generated server-side
    "name": str   # required, non-empty, unique
}
```

### Endpoints

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| POST | `/` | Create tag | 201 |
| GET | `/` | List all tags | 200 |
| POST | `/{tag_id}/tasks/{task_id}` | Attach tag to task | 200 |
| DELETE | `/{tag_id}/tasks/{task_id}` | Detach tag from task | 200 |

### Rules
- Use `miniapi.store.store.tags` dict for persistence (key = id)
- Tag names must be unique — return 409 `{"detail": "tag already exists"}` if duplicate
- POST `/` body: `{"name": "..."}`
- POST `/` returns the created tag dict with `id` included
- GET `/` returns a JSON list of all tag dicts
- POST `/{tag_id}/tasks/{task_id}` — attach:
  - Validate tag exists in `store.tags` — 404 `{"detail": "tag not found"}` if not
  - Validate task exists in `store.tasks` — 404 `{"detail": "task not found"}` if not
  - Append `tag_id` to `task["tag_ids"]` if not already present
  - Return `{"status": "attached"}`
- DELETE `/{tag_id}/tasks/{task_id}` — detach:
  - Same validations as attach
  - Remove `tag_id` from `task["tag_ids"]` if present
  - Return `{"status": "detached"}`

### Constraints
- Do NOT modify `app.py` or `store.py`
- Do NOT add new dependencies
- Do NOT create additional files
- Use Pydantic BaseModel for request validation
