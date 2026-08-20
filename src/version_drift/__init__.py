"""VersionDrift: standalone Git checkout drift detection and safe sync."""

from .core import (
    default_roots,
    discover_projects,
    inspect_project,
    record_event,
    scan_projects,
    summarize,
    sync_project,
)

__all__ = [
    "default_roots",
    "discover_projects",
    "inspect_project",
    "record_event",
    "scan_projects",
    "summarize",
    "sync_project",
]
__version__ = "0.1.0"
