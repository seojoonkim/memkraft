"""Minimal provenance APIs for MemKraft.

Stores append-only JSONL provenance records and explains records by resolving
source snippets where possible.  Stdlib-only and intentionally small.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class ProvenanceMixin:
    """Add provenance_record() and why() APIs to MemKraft."""

    def _provenance_path(self) -> Path:
        return self.base_dir / ".memkraft" / "provenance.jsonl"

    def provenance_record(
        self,
        record_id,
        derived_from,
        *,
        transform="",
        kind="",
        value="",
        confidence="",
    ) -> dict:
        """Append a normalized provenance record to .memkraft/provenance.jsonl."""
        record = {
            "record_id": str(record_id),
            "derived_from": self._provenance_normalize_sources(derived_from),
            "transform": transform,
            "kind": kind,
            "value": value,
            "confidence": confidence,
        }
        path = self._provenance_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def why(self, record_id) -> Optional[dict]:
        """Return latest provenance record for record_id with source snippets."""
        wanted = str(record_id)
        latest = None
        path = self._provenance_path()
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and str(rec.get("record_id")) == wanted:
                    latest = rec
        if latest is None:
            return None

        explained = dict(latest)
        sources = self._provenance_normalize_sources(explained.get("derived_from", []))
        explained["derived_from"] = [self._provenance_with_source_text(src) for src in sources]
        return explained

    def _provenance_normalize_sources(self, derived_from) -> List[dict]:
        if derived_from is None:
            items: Iterable[Any] = []
        elif isinstance(derived_from, dict):
            items = [derived_from]
        elif isinstance(derived_from, (str, Path)):
            items = [{"file": str(derived_from)}]
        else:
            try:
                items = list(derived_from)
            except TypeError:
                items = []

        normalized: List[dict] = []
        for item in items:
            if isinstance(item, dict):
                src = dict(item)
            elif isinstance(item, (str, Path)):
                src = {"file": str(item)}
            else:
                continue
            if "file" not in src and "path" in src:
                src["file"] = src["path"]
            if "file" in src:
                src["file"] = str(src["file"])
            normalized.append(src)
        return normalized

    def _provenance_with_source_text(self, source: dict) -> dict:
        out = dict(source)
        text = self._provenance_extract_text(source)
        if text is not None:
            out["source_text"] = text
        return out

    def _provenance_extract_text(self, source: dict) -> Optional[str]:
        raw_file = source.get("file") or source.get("path")
        if not raw_file:
            return None
        path = Path(str(raw_file))
        if not path.is_absolute():
            path = self.base_dir / path
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        span = source.get("span")
        if isinstance(span, (list, tuple)) and len(span) == 2:
            try:
                start, end = int(span[0]), int(span[1])
            except (TypeError, ValueError):
                return None
            if start < 0 or end < start or end > len(content):
                return None
            return content[start:end]

        line_range = source.get("lines", source.get("line_range"))
        if isinstance(line_range, (list, tuple)) and len(line_range) == 2:
            try:
                start_line, end_line = int(line_range[0]), int(line_range[1])
            except (TypeError, ValueError):
                return None
            lines = content.splitlines()
            if start_line < 1 or end_line < start_line or start_line > len(lines):
                return None
            return "\n".join(lines[start_line - 1 : min(end_line, len(lines))])

        return content
