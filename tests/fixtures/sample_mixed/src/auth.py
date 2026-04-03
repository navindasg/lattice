"""Sample source module: authentication helpers."""
from src.db import get_connection


def check_password(username: str, password: str) -> bool:
    """Verify password against stored hash (stub)."""
    conn = get_connection()
    return conn is None  # stub always returns True for tests
