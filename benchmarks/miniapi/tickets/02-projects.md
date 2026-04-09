# Ticket 02: Projects Module

## File to implement
`src/miniapi/projects.py`

## Tests to pass
`tests/test_projects.py` (4 tests)

## Spec

Implement CRUD endpoints for project management on the `router` APIRouter already defined in the stub.

### Data model
```python
{
    "id": str,         # UUID4, generated server-side
    "name": str,       # required, non-empty
    "owner_id": str    # required, must reference an existing user
}
```

### Endpoints

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| POST | `/` | Create project | 201 |
| GET | `/` | List all projects | 200 |
| GET | `/{project_id}` | Get single project | 200 or 404 |
| DELETE | `/{project_id}` | Delete project | 204 or 404 |

### Rules
- Use `miniapi.store.store.projects` dict for persistence (key = id)
- Use `miniapi.store.store.users` to validate that `owner_id` exists
- Generate UUID4 string IDs with `uuid.uuid4()`
- POST body: `{"name": "...", "owner_id": "..."}`
- If `owner_id` doesn't exist in `store.users`, return 400 `{"detail": "owner not found"}`
- POST returns the created project dict with `id` included
- GET `/` returns a JSON list of all project dicts
- GET `/{project_id}` returns the project dict or 404 `{"detail": "not found"}`
- DELETE `/{project_id}` returns 204 (no body) or 404 `{"detail": "not found"}`

### Constraints
- Do NOT modify `app.py` or `store.py`
- Do NOT add new dependencies
- Do NOT create additional files
- Use Pydantic BaseModel for request validation
