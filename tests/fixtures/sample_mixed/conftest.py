"""Pytest configuration for sample_mixed fixture.

Defines shared fixtures. This file should NOT be discovered as a test file.
"""
import pytest


@pytest.fixture
def db_session():
    """Provide a stub database session for tests."""
    return {"connected": True}
