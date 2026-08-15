"""Typed failures for the Project Memory Compiler."""
from typing import Any, Dict, Optional

from ..execution_protocol import ExecutionError

PROJECT_MEMORY_ERROR_REGISTRY = {
    "E_PM_VALIDATION": ("input", False),
    "E_PM_PATH_ESCAPE": ("input", False),
    "E_PM_LIMIT_EXCEEDED": ("input", False),
    "E_PM_NOT_BUILT": ("state", False),
    "E_PM_OWNERSHIP": ("integrity", False),
    "E_PM_SCHEMA_UNKNOWN": ("integrity", False),
    "E_PM_DIGEST_MISMATCH": ("integrity", False),
    "E_PM_SOURCE_UNREADABLE": ("io", False),
    "E_PM_STORE_BUSY": ("io", True),
}


class ProjectMemoryError(ExecutionError):
    def __init__(self, code: str, message: str,
                 details: Optional[Dict[str, Any]] = None):
        ValueError.__init__(self, message)
        self.code = code
        self.error_class, self.retryable = PROJECT_MEMORY_ERROR_REGISTRY[code]
        self.details = dict(details or {})


def fail(code: str, message: str, **details: Any) -> None:
    raise ProjectMemoryError(code, message, details)
