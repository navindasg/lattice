"""Integration tests for routes — imports routes, auth, and db."""
from src.auth import check_password
from src.db import get_connection
from src.routes import health_handler, login_handler


def test_login_handler_returns_dict() -> None:
    """login_handler returns a dict."""
    result = login_handler("user", "pass")
    assert isinstance(result, dict)
    assert "status" in result


def test_health_handler() -> None:
    """health_handler returns a healthy response."""
    result = health_handler()
    assert result["status"] == "healthy"
