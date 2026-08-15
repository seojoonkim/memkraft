"""Pure Markdown-to-evidence normalization."""
import hashlib
import re
from typing import Any, Dict, Iterable, List, Tuple

from .digest import digest_fields
from .errors import fail
from .model import normalized_limits

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")


def _content_digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def normalize_documents(files: Iterable[Tuple[str, str]],
                        limits: Any = None) -> List[Dict[str, Any]]:
    limits = normalized_limits(limits)
    ordered = sorted(list(files), key=lambda item: item[0])
    if len(ordered) > limits["max_files"]:
        fail("E_PM_LIMIT_EXCEEDED", "max_files exceeded")
    total = 0
    observations = []
    for path, text in ordered:
        raw = text.encode("utf-8")
        total += len(raw)
        if len(raw) > limits["max_file_bytes"]:
            fail("E_PM_LIMIT_EXCEEDED", "max_file_bytes exceeded", path=path)
        if total > limits["max_input_bytes"]:
            fail("E_PM_LIMIT_EXCEEDED", "max_input_bytes exceeded")
        lines = text.splitlines(keepends=True)
        if any(len(line.encode("utf-8")) > limits["max_line_bytes"] for line in lines):
            fail("E_PM_LIMIT_EXCEEDED", "max_line_bytes exceeded", path=path)
        starts = []
        heading = []
        for index, line in enumerate(lines):
            match = _HEADING.match(line.rstrip("\r\n"))
            if match:
                level = len(match.group(1))
                heading = heading[:level - 1] + [match.group(2)]
                starts.append((index, list(heading)))
        if not starts and lines:
            starts = [(0, [])]
        for position, (start, heading_path) in enumerate(starts):
            end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
            excerpt = "".join(lines[start:end])
            content_digest = _content_digest(excerpt.encode("utf-8"))
            observations.append({
                "schema_version": 1,
                "observation_id": digest_fields(path, start + 1, end, content_digest),
                "heading_path": heading_path,
                "excerpt": excerpt,
                "locator": {"path": path, "lines": [start + 1, end]},
                "content_digest": content_digest,
            })
    return sorted(observations, key=lambda row: (row["locator"]["path"], row["locator"]["lines"]))
