"""Event bus producer fixture — read via ast.parse(), not imported."""


class EventBus:
    """Simple event bus for pub/sub pattern."""

    def __init__(self):
        self._handlers = {}

    def emit(self, event_name, data=None):
        """Emit an event to all registered handlers."""
        handlers = self._handlers.get(event_name, [])
        for handler in handlers:
            handler(data)

    def on(self, event_name, handler_fn):
        """Register a handler for an event."""
        if event_name not in self._handlers:
            self._handlers[event_name] = []
        self._handlers[event_name].append(handler_fn)


bus = EventBus()


def create_user(data):
    """Create a user and emit an event."""
    bus.emit("user.created", data)
