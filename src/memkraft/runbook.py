"""Runbook Memory — MemKraft v0.9.0

Stores reusable remediation patterns (symptom → cause → fix) as MD files.
Pattern-matched against new symptoms to surface relevant past fixes.

API:
    mk.runbook_add(pattern, steps, ...) -> runbook_id
    mk.runbook_match(symptom, ...) -> list[dict]  (sorted by score desc)

Zero dependencies — stdlib only.
"""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage.incident_storage import (
    list_runbooks,
    make_runbook_id,
    now_iso,
    read_doc,
    runbook_path,
    write_doc,
)


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class RunbookMixin:
    """Adds runbook_* methods to :class:`MemKraft`."""

    def _runbook_path(self, runbook_id: str) -> Path:
        return runbook_path(self.base_dir, runbook_id)  # type: ignore[attr-defined]

    # --- API: add ----------------------------------------------------------

    def runbook_add(
        self,
        pattern: str,
        steps: List[str],
        *,
        source_incident_id: Optional[str] = None,
        source_incidents: Optional[List[str]] = None,
        cause: Optional[str] = None,
        evidence_cmd: Optional[str] = None,
        fix_action: Optional[str] = None,
        verification: Optional[str] = None,
        confidence: float = 0.5,
        tags: Optional[List[str]] = None,
    ) -> str:
        """Register (or upsert) a runbook keyed by its pattern.

        Upsert semantics: if a runbook with the same generated id already
        exists, steps/source_incidents are merged, usage_count bumped,
        confidence clamped to max(existing, new).
        """
        if not pattern or not str(pattern).strip():
            raise ValueError("pattern must be a non-empty string")
        if not steps or not isinstance(steps, list) or len(steps) == 0:
            raise ValueError("steps must be a non-empty list of strings")
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            raise ValueError("confidence must be a float in [0, 1]")
        if not (0.0 <= confidence <= 1.0):
            raise ValueError("confidence must be in [0, 1]")

        runbook_id = make_runbook_id(pattern)
        path = self._runbook_path(runbook_id)
        now = now_iso()

        merged_sources: List[str] = []
        if source_incidents:
            merged_sources.extend(source_incidents)
        if source_incident_id and source_incident_id not in merged_sources:
            merged_sources.append(source_incident_id)

        if path.exists():
            # upsert
            fm, heading, sections = read_doc(path)
            existing_sources = list(fm.get("source_incidents") or [])
            for s in merged_sources:
                if s not in existing_sources:
                    existing_sources.append(s)
            fm["source_incidents"] = existing_sources

            # merge steps (append new ones not already present)
            existing_steps_raw = sections.get("Steps", [])
            existing_step_text = {
                line.lstrip("- ").strip() for line in existing_steps_raw
            }
            for s in steps:
                if s not in existing_step_text:
                    sections.setdefault("Steps", []).append(f"- {s}")

            fm["confidence"] = max(float(fm.get("confidence", 0.0) or 0.0), confidence)
            fm["usage_count"] = int(fm.get("usage_count", 0) or 0) + 1
            fm["updated_at"] = now
            if cause and not sections.get("Cause"):
                sections["Cause"] = [cause.strip()]
            if evidence_cmd and not sections.get("Evidence Command"):
                sections["Evidence Command"] = [f"```bash\n{evidence_cmd.strip()}\n```"]
            if fix_action and not sections.get("Fix Action"):
                sections["Fix Action"] = [f"```bash\n{fix_action.strip()}\n```"]
            if verification and not sections.get("Verification"):
                sections["Verification"] = [f"```bash\n{verification.strip()}\n```"]
            if tags:
                cur = list(fm.get("tags") or [])
                for t in tags:
                    if t not in cur:
                        cur.append(t)
                fm["tags"] = cur
            write_doc(path, fm, heading=heading or pattern, sections=sections)
            return runbook_id

        # new runbook
        fm: Dict[str, Any] = {
            "id": runbook_id,
            "type": "runbook",
            "pattern": pattern.strip(),
            "confidence": confidence,
            "usage_count": 0,
            "source_incidents": merged_sources,
            "created_at": now,
            "updated_at": now,
            "last_matched": None,
            "tier": "recall",
            "tags": list(tags or []),
        }

        sections: Dict[str, List[str]] = {"": []}
        sections["Symptom"] = [pattern.strip()]
        if cause:
            sections["Cause"] = [cause.strip()]
        sections["Steps"] = [f"- {s}" for s in steps]
        if evidence_cmd:
            sections["Evidence Command"] = [f"```bash\n{evidence_cmd.strip()}\n```"]
        if fix_action:
            sections["Fix Action"] = [f"```bash\n{fix_action.strip()}\n```"]
        if verification:
            sections["Verification"] = [f"```bash\n{verification.strip()}\n```"]

        write_doc(path, fm, heading=pattern.strip(), sections=sections)
        return runbook_id

    # --- API: match --------------------------------------------------------

    def runbook_match(
        self,
        symptom: str,
        *,
        min_confidence: float = 0.0,
        min_score: float = 0.2,
        limit: int = 5,
        touch: bool = True,
    ) -> List[Dict[str, Any]]:
        """Find runbooks matching a symptom string.

        Score = 0.6 * text similarity + 0.4 * confidence.
        Regex patterns in the runbook are detected (heuristic: contains
        regex-meta) and evaluated with ``re.search`` — a regex hit yields
        similarity 1.0.
        """
        if not symptom or not str(symptom).strip():
            return []
        s = str(symptom).strip()
        s_low = s.lower()

        results: List[Dict[str, Any]] = []
        for p in list_runbooks(self.base_dir):  # type: ignore[attr-defined]
            try:
                fm, heading, sections = read_doc(p)
            except Exception:
                continue
            if fm.get("type") != "runbook":
                continue
            try:
                conf = float(fm.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf < min_confidence:
                continue

            pattern = str(fm.get("pattern") or "")
            # regex detection
            sim = _similarity(s_low, pattern.lower())
            if any(ch in pattern for ch in ".*+?[]|()"):
                try:
                    if re.search(pattern, s, flags=re.IGNORECASE):
                        sim = max(sim, 1.0)
                except re.error:
                    pass

            # include symptom-section text in similarity pool
            sec_text = " ".join(sections.get("Symptom", [])).lower()
            if sec_text:
                sim = max(sim, _similarity(s_low, sec_text))

            score = 0.6 * sim + 0.4 * conf
            if score < min_score:
                continue

            results.append({
                "id": fm.get("id"),
                "pattern": pattern,
                "confidence": conf,
                "usage_count": int(fm.get("usage_count", 0) or 0),
                "score": round(score, 4),
                "similarity": round(sim, 4),
                "source_incidents": list(fm.get("source_incidents") or []),
                "steps": [line.lstrip("- ").strip() for line in sections.get("Steps", [])],
                "path": str(p),
            })

        results.sort(key=lambda r: (r["score"], r["confidence"]), reverse=True)

        if touch and results:
            # bump usage_count + last_matched on the winner
            winner = results[0]
            try:
                path = Path(winner["path"])
                fm, heading, sections = read_doc(path)
                fm["usage_count"] = int(fm.get("usage_count", 0) or 0) + 1
                fm["last_matched"] = now_iso()
                # tiny confidence reinforcement (capped at 1.0)
                conf = float(fm.get("confidence", 0.0) or 0.0)
                fm["confidence"] = min(1.0, conf + 0.02)
                write_doc(path, fm, heading=heading or fm.get("pattern", ""), sections=sections)
                winner["usage_count"] = fm["usage_count"]
            except Exception:
                pass

        if limit and limit > 0:
            results = results[:limit]
        return results

    def runbook_get(self, runbook_id: str) -> Dict[str, Any]:
        path = self._runbook_path(runbook_id)
        if not path.exists():
            raise FileNotFoundError(f"runbook not found: {runbook_id}")
        fm, heading, sections = read_doc(path)
        return {
            "id": fm.get("id", runbook_id),
            "path": str(path),
            "frontmatter": dict(fm),
            "heading": heading,
            "sections": {k: list(v) for k, v in sections.items()},
        }
