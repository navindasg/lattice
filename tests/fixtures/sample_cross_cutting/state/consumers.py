"""Consumers of the shared global registry — read via ast.parse(), not imported."""
from state.registry import APP_REGISTRY, register


def setup_services():
    """Register core services into the global registry."""
    register("database", {"host": "localhost", "port": 5432})
    register("cache", {"host": "localhost", "port": 6379})


def get_service(name):
    """Retrieve a service from the global registry."""
    return APP_REGISTRY.get(name)
