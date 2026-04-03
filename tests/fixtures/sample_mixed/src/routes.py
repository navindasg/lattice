"""Sample source module: HTTP route handlers."""
from src.auth import check_password
from src.db import get_connection


def login_handler(username: str, password: str) -> dict:
    """Handle login request (stub)."""
    if check_password(username, password):
        return {"status": "ok", "user": username}
    return {"status": "error", "message": "Invalid credentials"}


def health_handler() -> dict:
    """Health check endpoint (stub)."""
    conn = get_connection()
    return {"status": "healthy", "db": conn is None}
