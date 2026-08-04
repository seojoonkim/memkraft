"""Deterministic, budget-bound context compiler preview API."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .execution_projection import project
from .store_core import read_all


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
        now: Optional[datetime] = None,
        pinned_sources: Optional[List[str]] = None,
        goal_id: Optional[str] = None,
        execution_budget: int = 0,
    ) -> Dict[str, Any]:
        task = _required_text(task, "task")
        if type(budget) is not int or budget <= 0:
            raise ValueError("budget must be a positive integer")
        if objective is not None:
            objective = _required_text(objective, "objective")
        if session_id is not None:
            session_id = _required_text(session_id, "session_id")
        if goal_id is not None:
            goal_id = _required_text(goal_id, "goal_id")
        if type(execution_budget) is not int or execution_budget < 0:
            raise ValueError("execution_budget must be a non-negative integer")
        if pinned_sources is not None and (not isinstance(pinned_sources, list) or any(
            not isinstance(source, str) or not source.strip() for source in pinned_sources
        )):
            raise ValueError("pinned_sources must be a list of non-empty strings")
        pinned = set(pinned_sources or [])
        utility = self.context_utility(now=now)

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
            item = {key: row[key] for key in ("id", "subject_id", "key", "value", "source") if key in row}
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
        execution_enabled = goal_id is not None and execution_budget > 0
        content_budget = budget - execution_budget if execution_enabled else budget
        for name, candidates in (("truth", truth), ("timeline", timeline), ("session", session)):
            baseline = {self._context_item_id(name, item): index for index, item in enumerate(candidates)}
            candidates.sort(key=lambda item: (
                0 if item.get("source") in pinned or item.get("provenance") in pinned else 1,
                -utility.get(self._context_item_id(name, item), {}).get("score", 0.0),
                baseline[self._context_item_id(name, item)],
            ))
            for item in candidates:
                proposed = {key: list(items) for key, items in sections.items()}
                proposed.setdefault(name, []).append(item)
                proposed_sources = _sources(proposed)
                proposed_tokens = _rendered_tokens(proposed, proposed_sources)
                if proposed_tokens <= content_budget:
                    sections = proposed
                    sources = proposed_sources
                    used = proposed_tokens
        if execution_enabled:
            execution_now = now or datetime.now(timezone.utc)
            execution_read = read_all(self._execution_events_path())
            projected = project(
                execution_read.records, execution_now, goal_id,
                skipped=execution_read.skipped,
            )
            execution: List[Dict[str, Any]] = []
            for gate in projected["gates"]:
                if gate["status"] != "pending" or not gate["required"]:
                    continue
                proposed_execution = execution + [gate]
                execution_tokens = _rendered_tokens(
                    {"execution": proposed_execution}, []
                )
                proposed = {key: list(items) for key, items in sections.items()}
                proposed["execution"] = proposed_execution
                proposed_tokens = _rendered_tokens(proposed, sources)
                if (len(proposed_execution) <= 8
                        and execution_tokens <= min(execution_budget, 400)
                        and proposed_tokens <= budget):
                    execution = proposed_execution
                    used = proposed_tokens
            if execution:
                sections["execution"] = execution
        identity_sections = {
            name: [{key: value for key, value in item.items() if key != "id"} for item in items]
            for name, items in sections.items()
        }
        identity = {
            "task": task, "budget": budget, "objective": objective,
            "session_id": session_id, "sections": identity_sections, "sources": sources,
        }
        usage_id = hashlib.sha256(json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        result = {
            "usage_id": usage_id,
            "task": task,
            "budget": budget,
            "estimated_tokens": used,
            "sections": sections,
            "sources": sources,
        }
        self._record_context_usage(result)
        return result
