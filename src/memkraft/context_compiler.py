"""Deterministic, budget-bound context compiler preview API."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def estimate_tokens(text: str) -> int:
    """Return a stable, dependency-free token estimate (four UTF-8 bytes/token)."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    size = len(text.encode("utf-8"))
    return (size + 3) // 4


def _rendered_tokens(sections: Dict[str, List[Dict[str, Any]]], sources: List[str]) -> int:
    if not sections and not sources:
        return 0
    rendered = json.dumps(
        {"sections": sections, "sources": sources},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return estimate_tokens(rendered)


def _sources(sections: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    cited = set()
    for items in sections.values():
        for item in items:
            if item.get("source") and item["source"] != "session_overlay":
                cited.add(str(item["source"]))
            if item.get("provenance") and item["provenance"] != "unknown":
                cited.add(str(item["provenance"]))
    return sorted(cited)


class ContextCompilerMixin:
    """Additive A1 compiler using canonical truth, timeline, and session overlay."""

    estimate_tokens = staticmethod(estimate_tokens)

    def compile_context(
        self,
        task: str,
        budget: int,
        objective: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        task = _required_text(task, "task")
        if type(budget) is not int or budget <= 0:
            raise ValueError("budget must be a positive integer")
        if objective is not None:
            objective = _required_text(objective, "objective")
        if session_id is not None:
            session_id = _required_text(session_id, "session_id")

        truth = []
        for row in self.compile_truth(dry_run=True)["records"]:
            item = {key: row[key] for key in ("subject_id", "key", "value", "source")}
            if row.get("valid_from") is not None:
                item["valid_from"] = row["valid_from"]
            if row.get("provenance") is not None:
                item["provenance"] = row["provenance"]
            truth.append(item)
        truth.sort(key=lambda row: (str(row["subject_id"]), str(row["key"])))

        timeline = []
        for row in self.timeline():
            if not row.get("source") and not row.get("provenance"):
                continue
            item = {key: row[key] for key in ("subject_id", "key", "value", "source") if key in row}
            for key in ("valid_from", "provenance"):
                if row.get(key) is not None:
                    item[key] = row[key]
            timeline.append(item)
        timeline.sort(key=lambda row: (
            str(row.get("valid_from") or ""),
            str(row.get("subject_id") or ""),
            str(row.get("key") or ""),
            json.dumps(row.get("value"), ensure_ascii=False, sort_keys=True, default=str),
        ))

        session = []
        if session_id is not None:
            for row in self.session_overlay(session_id, task, top_k=5):
                item = {
                    "candidate_id": row["candidate_id"],
                    "session_id": row["session_id"],
                    "text": row["text"],
                    "source": "session_overlay",
                    "provenance": row.get("provenance_id", "unknown"),
                }
                session.append(item)
            session.sort(key=lambda row: row["candidate_id"])

        sections: Dict[str, List[Dict[str, Any]]] = {}
        sources: List[str] = []
        used = _rendered_tokens(sections, sources)
        for name, candidates in (("truth", truth), ("timeline", timeline), ("session", session)):
            for item in candidates:
                proposed = {key: list(items) for key, items in sections.items()}
                proposed.setdefault(name, []).append(item)
                proposed_sources = _sources(proposed)
                proposed_tokens = _rendered_tokens(proposed, proposed_sources)
                if proposed_tokens <= budget:
                    sections = proposed
                    sources = proposed_sources
                    used = proposed_tokens
        identity = {
            "task": task, "budget": budget, "objective": objective,
            "session_id": session_id, "sections": sections, "sources": sources,
        }
        usage_id = hashlib.sha256(json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        return {
            "usage_id": usage_id,
            "task": task,
            "budget": budget,
            "estimated_tokens": used,
            "sections": sections,
            "sources": sources,
        }
