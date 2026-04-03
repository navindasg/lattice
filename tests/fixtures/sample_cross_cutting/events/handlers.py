"""Event bus consumer fixture — read via ast.parse(), not imported."""
from events.emitter import bus


def on_user_created(data):
    """Handler called when a user is created."""
    pass


def on_user_deleted(data):
    """Handler called when a user is deleted."""
    pass


bus.on("user.created", on_user_created)
bus.on("user.deleted", on_user_deleted)
