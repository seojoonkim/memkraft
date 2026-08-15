"""Pure Project Memory record validation."""
import re
from typing import Any, Dict

from ..execution_protocol import ExecutionError, canonical_timestamp
from .errors import fail

COMPILER_SCHEMA = 1
_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def normalize_as_of(value: Any) -> str:
    if isinstance(value, str) and "-00:00" in value:
        fail("E_PM_VALIDATION", "timestamp must use a known UTC offset")
    try:
        return canonical_timestamp(value)
    except (ExecutionError, TypeError, ValueError):
        fail("E_PM_VALIDATION", "timestamp must be strict aware RFC3339")


def validate_project_id(value: str) -> str:
    if not isinstance(value, str) or not _PROJECT_ID.fullmatch(value):
        fail("E_PM_VALIDATION", "project_id has an invalid form")
    return value


def normalized_limits(value: Any) -> Dict[str, int]:
    defaults = {"max_files": 10000, "max_input_bytes": 100000000,
                "max_file_bytes": 10000000, "max_line_bytes": 1000000}
    if value is None:
        return defaults
    if not isinstance(value, dict) or set(value) - set(defaults):
        fail("E_PM_VALIDATION", "limits contain unknown fields")
    for key, item in value.items():
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            fail("E_PM_VALIDATION", "%s must be a non-negative integer" % key)
        defaults[key] = item
    return defaults
