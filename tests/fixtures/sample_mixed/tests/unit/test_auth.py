"""Unit tests for auth module — imports only auth."""
from src.auth import check_password


def test_check_password_returns_bool() -> None:
    """check_password returns a boolean."""
    result = check_password("user", "pass")
    assert isinstance(result, bool)
