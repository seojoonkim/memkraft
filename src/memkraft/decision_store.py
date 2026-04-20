"""Decision Layer — MemKraft v0.9.1

Records product/architectural/operational decisions as first-class memory
with bitemporal semantics (when the decision was made, when we recorded
it) and optional bidirectional linking to incidents.

Inspired by YongKeun Park's "What/Why/How" principle: the decision page
captures *what* was decided, *why*, and *how* it's implemented, so future
agents can retrieve the full rationale instead of guessing.

API:
    mk.decision_record(what, why, how, ...) -> decision_id
    mk.decision_update(decision_id, ...) -> dict
    mk.decision_search(query=..., ...) -> list[dict]
    mk.decision_get(decision_id) -> dict
    mk.decision_link(decision_id, incident_id) -> None
    mk.evidence_first(query, limit=10) -> dict  # memory + incident + decision

Storage: ``memory/decisions/<id>.md`` — one MD file per decision.

Zero dependencies — stdlib only.
"""

from __future__ import annotations

import concurrent.futures as _cf
import contextlib
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage.incident_storage import (
    read_doc,
    write_doc,
    slugify,
    now_iso,
    today_iso,
)


# ---------------------------------------------------------------------------
# IDs + paths
# ---------------------------------------------------------------------------


def make_decision_id(what: str, decided_at: Optional[str] = None) -> str:
    date = (decided_at or today_iso())[:10]
    slug = slugify(what)
    return f"dec-{date}-{slug}"


def decisions_dir(base_dir: Path) -> Path:
    p = base_dir / "decisions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def decision_path(base_dir: Path, decision_id: str) -> Path:
    return decisions_dir(base_dir) / f"{decision_id}.md"


def list_decisions(base_dir: Path) -> List[Path]:
    d = decisions_dir(base_dir)
    return sorted(p for p in d.glob("*.md") if p.name != "_index.md")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

STATUSES = ("proposed", "accepted", "superseded", "rejected")


def _validate_status(s: str) -> str:
    if s not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {s!r}")
    return s


def _nonempty(value: Any, field: str) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{field} must be a non-empty string")
    return str(value).strip()


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class DecisionStoreMixin:
    """Adds ``decision_*`` + ``evidence_first`` methods to :class:`MemKraft`."""

    # --- internal helpers --------------------------------------------------
    # NOTE: ``decisions_dir`` already exists on MemKraft core as an instance
    # attribute pointing at ``base_dir / "decisions"``; we reuse that dir
    # rather than redefining the attribute.


    def _decision_path(self, decision_id: str) -> Path:
        return decision_path(self.base_dir, decision_id)  # type: ignore[attr-defined]

    def _decision_load(self, decision_id: str):
        path = self._decision_path(decision_id)
        if not path.exists():
            raise FileNotFoundError(f"decision not found: {decision_id}")
        return (path,) + read_doc(path)

    # --- API: record -------------------------------------------------------

    def decision_record(
        self,
        what: str,
        why: str,
        how: str,
        *,
        outcome: Optional[str] = None,
        tags: Optional[List[str]] = None,
        linked_incidents: Optional[List[str]] = None,
        status: str = "accepted",
        decided_at: Optional[str] = None,
        source: str = "manual",
        tier: Optional[str] = None,
    ) -> str:
        """Record a new decision. Returns the decision_id.

        Args:
            what: One-line decision (becomes title/slug).
            why: Rationale. Free-form text.
            how: Implementation/rollout notes. Free-form text.
            outcome: Optional observed outcome (can be set later via update).
            tags: Topic tags.
            linked_incidents: Incident IDs this decision relates to.
            status: One of ``proposed|accepted|superseded|rejected``.
            decided_at: ISO date/datetime; defaults to now.
            source: How this was captured (``manual``, ``sub-agent``, etc.).
            tier: Override tier (default: ``core`` for accepted/proposed,
                ``archival`` for superseded/rejected).
        """
        what_clean = _nonempty(what, "what")
        why_clean = _nonempty(why, "why")
        how_clean = _nonempty(how, "how")

        _validate_status(status)

        decided = decided_at or now_iso()
        base_id = make_decision_id(what_clean, decided_at=decided)
        decision_id = base_id
        suffix = 2
        while self._decision_path(decision_id).exists():
            decision_id = f"{base_id}-{suffix}"
            suffix += 1

        if tier is None:
            tier = "archival" if status in ("superseded", "rejected") else "core"

        now = now_iso()
        fm: Dict[str, Any] = {
            "id": decision_id,
            "type": "decision",
            "title": what_clean,
            "status": status,
            "decided_at": decided,
            "valid_from": decided,
            "valid_to": None,
            "recorded_at": now,
            "tier": tier,
            "source": source,
            "tags": list(tags or []),
            "linked_incidents": list(linked_incidents or []),
        }

        sections: Dict[str, List[str]] = {"": []}
        sections["What"] = [what_clean]
        sections["Why"] = [why_clean]
        sections["How"] = [how_clean]
        sections["Outcome"] = [outcome.strip()] if outcome and str(outcome).strip() else []
        sections["Linked Incidents"] = [f"- {iid}" for iid in (linked_incidents or [])]

        write_doc(self._decision_path(decision_id), fm, heading=what_clean, sections=sections)

        # Bidirectional link: if we have incident ids, append back-refs
        for iid in (linked_incidents or []):
            try:
                self._append_incident_backref(iid, decision_id)
            except Exception:
                # best effort — incident may not exist yet
                continue

        return decision_id

    def _append_incident_backref(self, incident_id: str, decision_id: str) -> None:
        """Add a back-reference to the incident's ``Related`` section."""
        try:
            from .storage.incident_storage import incident_path

            p = incident_path(self.base_dir, incident_id)  # type: ignore[attr-defined]
        except Exception:
            return
        if not p.exists():
            return
        fm, heading, sections = read_doc(p)
        related = sections.setdefault("Related", [])
        tag = f"- decision: {decision_id}"
        if tag not in related:
            related.append(tag)
        write_doc(p, fm, heading=heading or fm.get("title", "Incident"), sections=sections)

    # --- API: update -------------------------------------------------------

    def decision_update(
        self,
        decision_id: str,
        *,
        outcome: Optional[str] = None,
        append_why: Optional[str] = None,
        append_how: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[List[str]] = None,
        linked_incidents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update an existing decision in-place.

        ``outcome`` overwrites/extends the Outcome section (appends).
        ``append_why`` / ``append_how`` add text to Why/How (audit trail).
        ``status`` may be changed (e.g. accepted → superseded).
        """
        path, fm, heading, sections = self._decision_load(decision_id)
        now = now_iso()

        if status is not None:
            _validate_status(status)
            fm["status"] = status
            if status in ("superseded", "rejected"):
                fm["tier"] = "archival"
                fm["valid_to"] = now

        if outcome is not None and str(outcome).strip():
            sections.setdefault("Outcome", []).append(
                f"- [{now}] {outcome.strip()}"
            )

        if append_why:
            sections.setdefault("Why", []).append(f"- [{now}] {append_why.strip()}")

        if append_how:
            sections.setdefault("How", []).append(f"- [{now}] {append_how.strip()}")

        if tags:
            cur = list(fm.get("tags") or [])
            for t in tags:
                if t not in cur:
                    cur.append(t)
            fm["tags"] = cur

        if linked_incidents:
            cur_links = list(fm.get("linked_incidents") or [])
            linked_section = sections.setdefault("Linked Incidents", [])
            for iid in linked_incidents:
                if iid not in cur_links:
                    cur_links.append(iid)
                    linked_section.append(f"- {iid}")
                    try:
                        self._append_incident_backref(iid, decision_id)
                    except Exception:
                        pass
            fm["linked_incidents"] = cur_links

        fm["recorded_at"] = now

        write_doc(path, fm, heading=heading or fm.get("title", "Decision"), sections=sections)
        return dict(fm)

    # --- API: link ---------------------------------------------------------

    def decision_link(self, decision_id: str, incident_id: str) -> None:
        """Create a bidirectional link between a decision and an incident."""
        self.decision_update(decision_id, linked_incidents=[incident_id])

    # --- API: get ----------------------------------------------------------

    def decision_get(self, decision_id: str) -> Dict[str, Any]:
        """Return the full decision (frontmatter + sections)."""
        path, fm, heading, sections = self._decision_load(decision_id)
        return {
            "id": fm.get("id", decision_id),
            "path": str(path),
            "frontmatter": dict(fm),
            "heading": heading,
            "sections": {k: list(v) for k, v in sections.items()},
        }

    # --- API: search -------------------------------------------------------

    def decision_search(
        self,
        query: Optional[str] = None,
        *,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        linked_incident: Optional[str] = None,
        timeframe: Optional[Any] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Search decisions by filters.

        Matches ``query`` against title + why + how + outcome + tags
        (case-insensitive substring).
        """
        if status is not None:
            _validate_status(status)

        tf_from: Optional[str] = None
        tf_to: Optional[str] = None
        if timeframe is not None:
            try:
                tf_from, tf_to = timeframe
            except Exception:
                raise ValueError("timeframe must be a (from, to) tuple")

        q = (query or "").lower().strip() or None

        results: List[Dict[str, Any]] = []
        for p in list_decisions(self.base_dir):  # type: ignore[attr-defined]
            try:
                fm, heading, sections = read_doc(p)
            except Exception:
                continue
            if fm.get("type") != "decision":
                continue

            if status and fm.get("status") != status:
                continue
            if tag and tag not in (fm.get("tags") or []):
                continue
            if linked_incident and linked_incident not in (fm.get("linked_incidents") or []):
                continue

            decided = str(fm.get("decided_at") or "")
            if tf_from and decided < tf_from:
                continue
            if tf_to and decided > tf_to:
                continue

            if q:
                haystack = " ".join(
                    [
                        str(fm.get("title", "")),
                        " ".join(sections.get("Why", [])),
                        " ".join(sections.get("How", [])),
                        " ".join(sections.get("Outcome", [])),
                        " ".join(fm.get("tags") or []),
                    ]
                ).lower()
                if q not in haystack:
                    continue

            results.append(
                {
                    "id": fm.get("id"),
                    "title": fm.get("title"),
                    "status": fm.get("status"),
                    "decided_at": fm.get("decided_at"),
                    "tier": fm.get("tier"),
                    "tags": list(fm.get("tags") or []),
                    "linked_incidents": list(fm.get("linked_incidents") or []),
                    "path": str(p),
                }
            )

        results.sort(key=lambda r: str(r.get("decided_at") or ""), reverse=True)
        if limit and limit > 0:
            results = results[:limit]
        return results

    # --- API: evidence_first ----------------------------------------------

    def evidence_first(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Run memory_search + incident_search + decision_search in parallel.

        Returns a dict ``{query, elapsed_ms, counts, results}`` with the top
        ``limit`` merged results sorted by score (or detected_at/decided_at
        as a fallback). This implements YongKeun Park's "evidence first"
        principle natively in MemKraft.
        """
        import time

        if not query or not str(query).strip():
            raise ValueError("query must be non-empty")

        t0 = time.perf_counter()

        def _memory() -> List[Dict[str, Any]]:
            buf = io.StringIO()
            # suppress mk.search's built-in stdout pretty printing
            with contextlib.redirect_stdout(buf):
                raw = self.search(query) or []  # type: ignore[attr-defined]
            norm: List[Dict[str, Any]] = []
            for r in raw[:limit]:
                if isinstance(r, dict):
                    norm.append({**r, "_source": "memory"})
                else:
                    norm.append({"value": str(r), "_source": "memory"})
            return norm

        def _incident() -> List[Dict[str, Any]]:
            try:
                raw = self.incident_search(query, limit=limit) or []  # type: ignore[attr-defined]
            except AttributeError:
                return []
            return [{**r, "_source": "incident"} for r in raw]

        def _decision() -> List[Dict[str, Any]]:
            try:
                raw = self.decision_search(query, limit=limit) or []
            except AttributeError:
                return []
            return [{**r, "_source": "decision"} for r in raw]

        with _cf.ThreadPoolExecutor(max_workers=3) as ex:
            futures = {
                "memory": ex.submit(_memory),
                "incident": ex.submit(_incident),
                "decision": ex.submit(_decision),
            }
            buckets = {name: fut.result() for name, fut in futures.items()}

        merged: List[Dict[str, Any]] = []
        for bucket in buckets.values():
            merged.extend(bucket)

        def _sort_key(item: Dict[str, Any]) -> Any:
            score = item.get("score")
            if isinstance(score, (int, float)):
                return (1, score)
            ts = item.get("detected_at") or item.get("decided_at") or ""
            return (0, str(ts))

        merged.sort(key=_sort_key, reverse=True)
        top = merged[:limit]

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "query": query,
            "elapsed_ms": elapsed_ms,
            "counts": {name: len(items) for name, items in buckets.items()},
            "total_merged": len(merged),
            "top_n": len(top),
            "results": top,
        }
