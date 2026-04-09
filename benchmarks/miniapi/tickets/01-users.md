# Ticket 01: Users Module

## File to implement
`src/miniapi/users.py`

## Tests to pass
`tests/test_users.py` (4 tests)

## Spec

Implement CRUD endpoints for user management on the `router` APIRouter already defined in the stub.

### Data model
```python
{
    "id": str,        # UUID4, generated server-side
    "name": str,      # required, non-empty
    "email": str      # required, must contain "@"
}
```

### Endpoints

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| POST | `/` | Create user | 201 |
| GET | `/` | List all users | 200 |
| GET | `/{user_id}` | Get single user | 200 or 404 |
| DELETE | `/{user_id}` | Delete user | 204 or 404 |

### Rules
- Use `miniapi.store.store.users` dict for persistence (key = id)
- Generate UUID4 string IDs with `uuid.uuid4()`
- POST body: `{"name": "...", "email": "..."}`
- POST returns the created user dict with `id` included
- GET `/` returns a JSON list of all user dicts
- GET `/{user_id}` returns the user dict or 404 `{"detail": "not found"}`
- DELETE `/{user_id}` returns 204 (no body) or 404 `{"detail": "not found"}`
- Validate: name must be non-empty string, email must contain "@"
- Return 422 for invalid input (FastAPI handles this via Pydantic)

### Constraints
- Do NOT modify `app.py` or `store.py`
- Do NOT add new dependencies
- Do NOT create additional files
- Use Pydantic BaseModel for request validation
