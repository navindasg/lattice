"""Shared global registry fixture — read via ast.parse(), not imported."""

APP_REGISTRY = {}


def register(name, component):
    """Register a component by name."""
    APP_REGISTRY[name] = component


def get(name):
    """Retrieve a registered component by name."""
    return APP_REGISTRY.get(name)


def unregister(name):
    """Remove a component from the registry."""
    APP_REGISTRY.pop(name, None)
