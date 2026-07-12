"""Candidate memory APIs for MemKraft 2.13.

Preview tier: this module stores short-lived candidate memories only.  It does
not promote, resolve, search global memory, or extract claims in S10.
"""

from __future__ import annotations
import typing

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import re
import uuid

from .claims import extract_claims
from .store_core import append, read_all

DEFAULT_CANDIDATE_TTL_HOURS = 24
_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)


class CandidateMixin:
    """Add preview candidate memory capture/list APIs to MemKraft."""

    def _candidates_path(self) -> Path:
        return self.base_dir / ".memkraft" / "candidates.jsonl"

    def remember_candidate(
        self,
        text: str,
        *,
        session_id: str,
        entity_hint: typing.Optional[str] = None,
        provenance_id: typing.Optional[str] = None,
        expires_at: typing.Optional[str] = None,
    ) -> dict[str, Any]:
        """Preview: append a short-lived candidate memory and return its id."""
        expires = expires_at or (
            datetime.now(timezone.utc) + timedelta(hours=DEFAULT_CANDIDATE_TTL_HOURS)
        ).isoformat(timespec="seconds")
        candidate_id = uuid.uuid4().hex
        claims = extract_claims(str(text), entity_hint=entity_hint)
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
                "claims": claims,
                "review_state": "READY_FOR_RESOLVER" if claims else "CANDIDATE_REVIEW",
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
        session_id: typing.Optional[str] = None,
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

    def session_overlay(
        self,
        session_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Preview: search same-session candidate memories by token overlap only."""
        query_tokens = _tokens(query)
        if not query_tokens or int(top_k) <= 0:
            return []
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for record in self.list_candidates(session_id=str(session_id)):
            text_tokens = _tokens(record.get("text", ""))
            score = len(query_tokens & text_tokens)
            if score <= 0:
                continue
            item = {
                "candidate_id": record["candidate_id"],
                "session_id": record["session_id"],
                "text": record["text"],
                "score": score,
                "memory_state": "session_overlay",
                "expires_at": record.get("expires_at"),
                "provenance_id": record.get("provenance_id", "unknown"),
                "claims": record.get("claims", []),
            }
            scored.append((score, str(record.get("created_at", "")), item))
        scored.sort(key=lambda row: (-row[0], row[1], row[2]["candidate_id"]))
        return self._validate_session_overlay_results([item for _, _, item in scored[: int(top_k)]])

    def _validate_session_overlay_results(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Internal: enforce the session_overlay memory_state label schema."""
        for item in results:
            if item.get("memory_state") != "session_overlay":
                raise ValueError("session_overlay result missing memory_state='session_overlay'")
        return results


def _tokens(value: Any) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(str(value))}


def _is_expired(expires_at: Any, now: datetime) -> bool:
    try:
        expires = datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError):
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= now
