"""Structured, provenance-aware artifact persistence and retrieval."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


_PROVENANCE_FIELDS = (
    "session_id", "parent_session_id", "lineage_key", "platform", "chat_id",
    "thread_id", "account_id", "profile", "agent_identity",
    "user_platform_message_id", "assistant_platform_message_id", "timestamp",
    "origin_kind",
)
_DIRECT_ORIGINS = {"direct", "original"}
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _timestamp_value(value: Any) -> float:
    """Normalize ISO-8601 timestamps for chronological ordering."""
    if not value:
        return 0.0
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def normalize_provenance(provenance: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
    """Return the stable public provenance schema, omitting unavailable fields."""
    supplied = dict(provenance or {})
    if not supplied.get("profile") and supplied.get("agent_profile"):
        supplied["profile"] = supplied["agent_profile"]
    for role in ("user", "assistant"):
        canonical = role + "_platform_message_id"
        legacy = role + "_message_id"
        if not supplied.get(canonical) and supplied.get(legacy) is not None:
            supplied[canonical] = supplied[legacy]
    return {
        field: str(supplied[field])
        for field in _PROVENANCE_FIELDS
        if supplied.get(field) is not None and str(supplied[field]) != ""
    }


class ArtifactMixin:
    """Public API for durable completed-content artifacts."""

    def persist_artifact(
        self,
        content: str,
        *,
        provenance: Optional[Mapping[str, Any]] = None,
        source: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Persist content and normalized provenance as an independently addressable artifact."""
        if not content or not content.strip():
            return None
        artifact_id = uuid.uuid4().hex
        directory = self.base_dir / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        metadata_path = directory / (artifact_id + ".json")
        content_path = directory / (artifact_id + ".md")
        normalized = normalize_provenance(provenance)
        record = {
            "id": artifact_id,
            "content": content,
            "provenance": normalized,
            "source": source,
            "content_file": str(content_path.relative_to(self.base_dir)),
        }
        # Content may land first, but metadata is published only by atomic
        # rename. If metadata serialization fails, remove both temporary and
        # content files so neither artifact search nor the ordinary corpus sees
        # a half-committed record.
        metadata_tmp = directory / (".{}.json.tmp".format(artifact_id))
        try:
            fd = os.open(str(content_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            fd = os.open(str(metadata_tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(metadata_tmp), str(metadata_path))
            directory_fd = os.open(str(directory), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            metadata_tmp.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            content_path.unlink(missing_ok=True)
            raise

        from ._corpus_index import invalidate as invalidate_corpus
        from ._read_cache import get_cache
        get_cache().invalidate(content_path)
        invalidate_corpus(content_path)
        bump = getattr(self, "_bump_cache_generation", None)
        if callable(bump):
            bump()
        return self._artifact_result(record, metadata_path, score=1.0)

    def search_artifacts(
        self,
        query: str,
        *,
        exact_phrase: bool = True,
        provenance: Optional[Mapping[str, Any]] = None,
        scope_filters: Optional[Mapping[str, Any]] = None,
        order: str = "newest",
        prefer_direct: bool = True,
        top_k: Any = None,
    ) -> List[Dict[str, Any]]:
        """Search artifacts by phrase with optional exact provenance scope."""
        if not query or not query.strip():
            return []
        combined_filters: Dict[str, Any] = {}
        combined_filters.update(provenance or {})
        combined_filters.update(scope_filters or {})
        filters = normalize_provenance_filters(combined_filters)
        query_lower = query.casefold()
        query_tokens = set(_TOKEN_RE.findall(query_lower))
        found = []
        directory = self.base_dir / "artifacts"
        for path in directory.glob("*.json") if directory.exists() else ():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                content = str(record.get("content") or "")
                record_provenance = record.get("provenance") or {}
            except (OSError, ValueError, TypeError):
                continue
            if any(str(record_provenance.get(key, "")) != value for key, value in filters.items()):
                continue
            content_lower = content.casefold()
            phrase_hit = query_lower in content_lower
            content_tokens = set(_TOKEN_RE.findall(content_lower))
            overlap = len(query_tokens & content_tokens) / max(1, len(query_tokens))
            if exact_phrase and not phrase_hit:
                continue
            if not exact_phrase and not phrase_hit and overlap == 0:
                continue
            # Phrase hits are distinct but remain below 1.0, leaving room for
            # deterministic tie-breaks without saturating unrelated results.
            score = 0.9 + min(0.09, overlap * 0.09) if phrase_hit else overlap * 0.7
            found.append(self._artifact_result(record, path, score=round(score, 4)))

        newest_first = order != "oldest"

        def rank(item: Dict[str, Any]):
            prov = item["provenance"]
            direct = prov.get("origin_kind", "").casefold() in _DIRECT_ORIGINS
            timestamp = _timestamp_value(prov.get("timestamp", ""))
            ordered_time = timestamp if newest_first else -timestamp
            return (
                1 if prefer_direct and direct else 0,
                item["score"],
                ordered_time,
                item["source_handle"],
            )

        found.sort(key=rank, reverse=True)
        return found[:top_k] if isinstance(top_k, int) and top_k > 0 else found

    def _artifact_result(self, record: Mapping[str, Any], path: Path, score: float) -> Dict[str, Any]:
        return {
            "content": str(record.get("content") or ""),
            "provenance": dict(record.get("provenance") or {}),
            "source_handle": "artifact:{}".format(record.get("id") or path.stem),
            "source": str(record.get("source") or record.get("content_file") or path),
            "score": score,
        }


def normalize_provenance_filters(provenance: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    supplied = dict(provenance or {})
    if not supplied.get("profile") and supplied.get("agent_profile"):
        supplied["profile"] = supplied["agent_profile"]
    return {
        field: str(supplied[field])
        for field in _PROVENANCE_FIELDS
        if field in supplied and supplied[field] is not None and str(supplied[field]) != ""
    }


__all__ = ["ArtifactMixin", "normalize_provenance"]
