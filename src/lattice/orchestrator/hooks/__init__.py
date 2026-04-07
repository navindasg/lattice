"""CC hook installer subpackage for lattice.orchestrator.

Provides automatic configuration of Claude Code HTTP hooks that push events
to the Lattice orchestrator. Supports install, uninstall, and status checks.

All public classes are re-exported here:
    from lattice.orchestrator.hooks import HookInstaller, HookDefinition
"""
from lattice.orchestrator.hooks.constants import (
    CC_HOOK_DEFINITIONS,
    HOOK_EVENT_TYPES,
    LATTICE_HOOK_MARKER,
)
from lattice.orchestrator.hooks.installer import HookInstaller
from lattice.orchestrator.hooks.models import (
    HookCheckResult,
    HookDefinition,
    HookEventStatus,
    HookInstallResult,
    HookUninstallResult,
)

__all__ = [
    "CC_HOOK_DEFINITIONS",
    "HOOK_EVENT_TYPES",
    "HookCheckResult",
    "HookDefinition",
    "HookEventStatus",
    "HookInstallResult",
    "HookInstaller",
    "HookUninstallResult",
    "LATTICE_HOOK_MARKER",
]
