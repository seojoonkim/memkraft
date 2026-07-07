"""Candidate memory APIs for MemKraft 2.13.

Preview tier: this module stores short-lived candidate memories only.  It does
not promote, resolve, search global memory, or extract claims in S10.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import uuid

from .store_core import append, read_all

DEFAULT_CANDIDATE_TTL_HOURS = 24


class CandidateMixin:
    """Add preview candidate memory capture/list APIs to MemKraft."""

    def _candidates_path(self) -> Path:
        return self.base_dir / ".memkraft" / "candidates.jsonl"

    def remember_candidate(
        self,
        text: str,
        *,
        session_id: str,
        entity_hint: str | None = None,
        provenance_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Preview: append a short-lived candidate memory and return its id."""
        expires = expires_at or (
            datetime.now(timezone.utc) + timedelta(hours=DEFAULT_CANDIDATE_TTL_HOURS)
        ).isoformat(timespec="seconds")
        candidate_id = uuid.uuid4().hex
        record = append(
            self._candidates_path(),
            {
                "id": candidate_id,
                "candidate_id": candidate_id,
                "kind": "candidate_memory",
                "text": str(text),
                "session_id": str(session_id),
                "entity_hint": entity_hint,
                "expires_at": expires,
                "provenance_id": provenance_id or "unknown",
                "claims": [],
            },
        )
        return {
            "candidate_id": record["id"],
            "expires_at": record["expires_at"],
            "provenance_id": record["provenance_id"],
        }

    def list_candidates(
        self,
        *,
        session_id: str | None = None,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        """Preview: list candidate memory records, excluding expired by default."""
        now = datetime.now(timezone.utc)
        out: list[dict[str, Any]] = []
        for record in read_all(self._candidates_path()).records:
            if record.get("kind") != "candidate_memory":
                continue
            if session_id is not None and record.get("session_id") != session_id:
                continue
            if not include_expired and _is_expired(record.get("expires_at"), now):
                continue
            normalized = dict(record)
            normalized.setdefault("candidate_id", normalized.get("id"))
            out.append(normalized)
        return out


def _is_expired(expires_at: Any, now: datetime) -> bool:
    try:
        expires = datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError):
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= now
