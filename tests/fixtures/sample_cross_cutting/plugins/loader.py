"""Plugin loader using importlib.metadata fixture — read via ast.parse(), not imported."""
import importlib.metadata

# Module-level plugin discovery with literal group name — statically detectable
PLUGINS = importlib.metadata.entry_points(group="myapp.plugins")


def load_plugins(group="myapp.plugins"):
    """Load all registered plugins for the given entry_points group."""
    plugins = []
    for ep in importlib.metadata.entry_points(group=group):
        plugin_cls = ep.load()
        plugins.append(plugin_cls)
    return plugins


def get_plugin_names(group="myapp.plugins"):
    """Return a list of plugin names registered for the given group."""
    return [ep.name for ep in importlib.metadata.entry_points(group=group)]
