"""Incident Memory Layer — MemKraft v0.9.0

Records operational incidents as first-class memory with bitemporal
semantics, tier auto-assignment, and evidence-backed structure inspired
by OpenSRE.

API:
    mk.incident_record(title, symptoms, ...) -> incident_id
    mk.incident_update(incident_id, ...) -> None
    mk.incident_search(query=..., ...) -> list[dict]

Storage: ``memory/incidents/<id>.md`` — one MD file per incident.

Zero dependencies — stdlib only.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage.incident_storage import (
    incident_path,
    incidents_dir,
    list_incidents,
    make_incident_id,
    now_iso,
    today_iso,
    read_doc,
    write_doc,
)


SEVERITIES = ("low", "medium", "high", "critical")
STATUSES = ("open", "resolved")


def _now() -> str:
    return now_iso()


def _validate_severity(s: str) -> str:
    if s not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}, got {s!r}")
    return s


class IncidentMixin:
    """Adds incident_* methods to :class:`MemKraft`."""

    # --- internal helpers --------------------------------------------------

    def _incident_path(self, incident_id: str) -> Path:
        return incident_path(self.base_dir, incident_id)  # type: ignore[attr-defined]

    def _incident_load(self, incident_id: str):
        path = self._incident_path(incident_id)
        if not path.exists():
            raise FileNotFoundError(f"incident not found: {incident_id}")
        return (path,) + read_doc(path)

    # --- API: record -------------------------------------------------------

    def incident_record(
        self,
        title: str,
        symptoms: Optional[List[str]] = None,
        *,
        evidence: Optional[List[Dict[str, Any]]] = None,
        hypothesis: Optional[List[str]] = None,
        resolution: Optional[str] = None,
        severity: str = "medium",
        affected: Optional[List[str]] = None,
        detected_at: Optional[str] = None,
        source: str = "manual",
        tags: Optional[List[str]] = None,
        tier: Optional[str] = None,
    ) -> str:
        """Record a new incident.

        Returns the incident_id. Idempotent on id collision: if the
        generated id already exists, a ``-2``/``-3``/... suffix is appended.
        """
        if not title or not str(title).strip():
            raise ValueError("title must be a non-empty string")
        if symptoms is None or len(symptoms) == 0:
            raise ValueError("symptoms must be a non-empty list")
        if not isinstance(symptoms, list):
            raise TypeError("symptoms must be a list of strings")

        _validate_severity(severity)

        detected = detected_at or _now()
        base_id = make_incident_id(title, detected_at=detected)
        incident_id = base_id
        suffix = 2
        while self._incident_path(incident_id).exists():
            incident_id = f"{base_id}-{suffix}"
            suffix += 1

        # decide tier: resolved-on-creation → archival, else core for open
        # incidents so the agent sees them in working_set.
        resolved = resolution is not None and str(resolution).strip() != ""
        status = "resolved" if resolved else "open"
        if tier is None:
            tier = "archival" if resolved else "core"

        now = _now()
        fm: Dict[str, Any] = {
            "id": incident_id,
            "type": "incident",
            "title": title.strip(),
            "severity": severity,
            "status": status,
            "detected_at": detected,
            "resolved_at": now if resolved else None,
            "valid_from": detected,
            "valid_to": None,
            "recorded_at": now,
            "tier": tier,
            "source": source,
            "affected": list(affected or []),
            "tags": list(tags or []),
        }

        sections: Dict[str, List[str]] = {"": []}
        sections["Symptoms"] = [f"- {s}" for s in symptoms]

        ev_lines: List[str] = []
        for e in evidence or []:
            if isinstance(e, dict):
                parts = [f"{k}: {v}" for k, v in e.items()]
                ev_lines.append("- " + ", ".join(parts))
            else:
                ev_lines.append(f"- {e}")
        sections["Evidence"] = ev_lines

        hyp_lines: List[str] = []
        if hypothesis:
            sections["Hypotheses"] = [f"- [testing] {h}" for h in hypothesis]
        else:
            sections["Hypotheses"] = []

        sections["Resolution"] = ([resolution.strip()] if resolved else [])

        sections["Related"] = []

        write_doc(self._incident_path(incident_id), fm, heading=title.strip(), sections=sections)
        return incident_id

    # --- API: update -------------------------------------------------------

    def incident_update(
        self,
        incident_id: str,
        *,
        add_symptoms: Optional[List[str]] = None,
        add_evidence: Optional[List[Dict[str, Any]]] = None,
        add_hypothesis: Optional[List[str]] = None,
        reject_hypothesis: Optional[List[str]] = None,
        confirm_hypothesis: Optional[List[str]] = None,
        resolution: Optional[str] = None,
        resolved: Optional[bool] = None,
        severity: Optional[str] = None,
        tags: Optional[List[str]] = None,
        affected: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update an incident in-place.

        All list-adds append (deduped, preserving order). ``resolution``
        setting auto-flips ``resolved=True`` unless explicitly overridden.

        Returns the updated frontmatter dict.
        """
        path, fm, heading, sections = self._incident_load(incident_id)
        now = _now()

        if severity is not None:
            _validate_severity(severity)
            fm["severity"] = severity

        if add_symptoms:
            existing = set(line.lstrip("- ").strip() for line in sections.get("Symptoms", []))
            for s in add_symptoms:
                if s not in existing:
                    sections.setdefault("Symptoms", []).append(f"- {s}")
                    existing.add(s)

        if add_evidence:
            for e in add_evidence:
                if isinstance(e, dict):
                    parts = [f"{k}: {v}" for k, v in e.items()]
                    sections.setdefault("Evidence", []).append("- " + ", ".join(parts))
                else:
                    sections.setdefault("Evidence", []).append(f"- {e}")

        if add_hypothesis:
            for h in add_hypothesis:
                sections.setdefault("Hypotheses", []).append(f"- [testing] {h}")

        if reject_hypothesis:
            new_lines: List[str] = []
            reject_set = set(reject_hypothesis)
            for line in sections.get("Hypotheses", []):
                updated = False
                for h in reject_set:
                    if h in line:
                        new_lines.append(f"- [rejected @ {now}] {h}")
                        updated = True
                        break
                if not updated:
                    new_lines.append(line)
            # also add any that weren't present
            existing_text = "\n".join(new_lines)
            for h in reject_set:
                if h not in existing_text:
                    new_lines.append(f"- [rejected @ {now}] {h}")
            sections["Hypotheses"] = new_lines

        if confirm_hypothesis:
            new_lines = []
            confirm_set = set(confirm_hypothesis)
            for line in sections.get("Hypotheses", []):
                updated = False
                for h in confirm_set:
                    if h in line:
                        new_lines.append(f"- [confirmed @ {now}] {h}")
                        updated = True
                        break
                if not updated:
                    new_lines.append(line)
            existing_text = "\n".join(new_lines)
            for h in confirm_set:
                if h not in existing_text:
                    new_lines.append(f"- [confirmed @ {now}] {h}")
            sections["Hypotheses"] = new_lines

        if tags:
            cur = list(fm.get("tags") or [])
            for t in tags:
                if t not in cur:
                    cur.append(t)
            fm["tags"] = cur

        if affected:
            cur = list(fm.get("affected") or [])
            for a in affected:
                if a not in cur:
                    cur.append(a)
            fm["affected"] = cur

        will_resolve = False
        if resolution is not None and str(resolution).strip():
            sections.setdefault("Resolution", []).append(resolution.strip())
            if resolved is None:
                will_resolve = True
        if resolved is True:
            will_resolve = True
        if resolved is False:
            fm["status"] = "open"
            fm["resolved_at"] = None
            fm["tier"] = fm.get("tier") if fm.get("tier") != "archival" else "core"

        if will_resolve:
            fm["status"] = "resolved"
            fm["resolved_at"] = now
            fm["valid_to"] = now
            # auto-demote to archival
            fm["tier"] = "archival"

        # bitemporal: always bump recorded_at
        fm["recorded_at"] = now

        write_doc(path, fm, heading=heading or fm.get("title", "Incident"), sections=sections)
        return dict(fm)

    # --- API: search -------------------------------------------------------

    def incident_search(
        self,
        query: Optional[str] = None,
        *,
        pattern: Optional[str] = None,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        resolved: Optional[bool] = None,
        affected: Optional[str] = None,
        timeframe: Optional[Any] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Search incidents by a variety of filters.

        ``query`` / ``pattern`` match against title + symptoms + evidence
        (case-insensitive substring). ``resolved`` is a shortcut for
        ``status=\"resolved\"/\"open\"``. ``timeframe`` is an optional
        (from, to) tuple of ISO strings.

        Returns a list of summary dicts sorted newest first.
        """
        if severity is not None and severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        if resolved is True:
            status = status or "resolved"
        elif resolved is False:
            status = status or "open"

        tf_from: Optional[str] = None
        tf_to: Optional[str] = None
        if timeframe is not None:
            try:
                tf_from, tf_to = timeframe
            except Exception:
                raise ValueError("timeframe must be a (from, to) tuple")

        q = (query or pattern or "").lower().strip() or None

        results: List[Dict[str, Any]] = []
        for p in list_incidents(self.base_dir):  # type: ignore[attr-defined]
            try:
                fm, heading, sections = read_doc(p)
            except Exception:
                continue
            if fm.get("type") != "incident":
                continue

            if severity and fm.get("severity") != severity:
                continue
            if status and fm.get("status") != status:
                continue
            if affected and affected not in (fm.get("affected") or []):
                continue

            detected = str(fm.get("detected_at") or "")
            if tf_from and detected < tf_from:
                continue
            if tf_to and detected > tf_to:
                continue

            if q:
                haystack = " ".join([
                    str(fm.get("title", "")),
                    " ".join(sections.get("Symptoms", [])),
                    " ".join(sections.get("Evidence", [])),
                    " ".join(fm.get("tags") or []),
                ]).lower()
                if q not in haystack:
                    continue

            results.append({
                "id": fm.get("id"),
                "title": fm.get("title"),
                "severity": fm.get("severity"),
                "status": fm.get("status"),
                "detected_at": fm.get("detected_at"),
                "resolved_at": fm.get("resolved_at"),
                "affected": list(fm.get("affected") or []),
                "tags": list(fm.get("tags") or []),
                "tier": fm.get("tier"),
                "path": str(p),
            })

        results.sort(key=lambda r: str(r.get("detected_at") or ""), reverse=True)
        if limit and limit > 0:
            results = results[:limit]
        return results

    def incident_get(self, incident_id: str) -> Dict[str, Any]:
        """Return the full incident as a dict (frontmatter + sections)."""
        path, fm, heading, sections = self._incident_load(incident_id)
        return {
            "id": fm.get("id", incident_id),
            "path": str(path),
            "frontmatter": dict(fm),
            "heading": heading,
            "sections": {k: list(v) for k, v in sections.items()},
        }
