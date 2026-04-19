"""Root-Cause Analysis — MemKraft v0.9.0

Heuristic scoring of existing hypotheses for an incident, enriched with:
* evidence volume (more evidence → slight confidence bump)
* matching runbooks (symptoms → past fixes)
* related incidents (same affected components or overlapping symptoms)

No external LLM call — strategy="heuristic" is the only v0.9 strategy.
Returns a structured report dict.

Zero dependencies — stdlib only.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage.incident_storage import list_incidents, read_doc


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class RCAMixin:
    """Adds incident_rca to :class:`MemKraft`."""

    def incident_rca(
        self,
        incident_id: str,
        *,
        strategy: str = "heuristic",
        include_related: bool = True,
        max_related: int = 3,
    ) -> Dict[str, Any]:
        """Produce an RCA report for ``incident_id``.

        Returns::

            {
              "incident_id": ...,
              "strategy": "heuristic",
              "hypotheses": [  # scored & sorted desc
                 {"text": ..., "status": testing|rejected|confirmed,
                  "score": 0.0..1.0}
              ],
              "suggested_runbooks": [...],   # from runbook_match
              "related_incidents": [...],    # only if include_related
            }
        """
        if strategy != "heuristic":
            # v0.9: only heuristic. Other strategies reserved.
            raise ValueError(
                "strategy must be 'heuristic' in v0.9.0 (llm/both reserved)"
            )

        # Need incident_get from IncidentMixin — duck-typed
        get = getattr(self, "incident_get", None)
        if not callable(get):
            raise RuntimeError("incident_rca requires IncidentMixin to be attached")
        data = get(incident_id)
        fm = data["frontmatter"]
        sections = data["sections"]

        symptoms = [
            line.lstrip("- ").strip()
            for line in sections.get("Symptoms", [])
            if line.strip()
        ]
        evidence_lines = [
            line for line in sections.get("Evidence", []) if line.strip()
        ]
        hyp_lines = [
            line for line in sections.get("Hypotheses", []) if line.strip()
        ]

        # Parse hypotheses with status
        parsed_hypotheses: List[Dict[str, Any]] = []
        for line in hyp_lines:
            # Expected shape: "- [testing] text" or "- [rejected @ ts] text"
            text = line.lstrip("- ").strip()
            status = "testing"
            if text.startswith("[rejected"):
                status = "rejected"
                text = text.split("]", 1)[-1].strip()
            elif text.startswith("[confirmed"):
                status = "confirmed"
                text = text.split("]", 1)[-1].strip()
            elif text.startswith("[testing]"):
                text = text[len("[testing]"):].strip()
            parsed_hypotheses.append({"text": text, "status": status})

        # Score each hypothesis heuristically
        # Base score:
        #   confirmed  → 0.95
        #   rejected   → 0.05
        #   testing    → symptom overlap similarity + evidence bonus
        evidence_bonus = min(0.25, 0.05 * len(evidence_lines))
        symptom_blob = " ".join(symptoms).lower()

        scored: List[Dict[str, Any]] = []
        for h in parsed_hypotheses:
            if h["status"] == "confirmed":
                score = 0.95
            elif h["status"] == "rejected":
                score = 0.05
            else:
                base = _sim(symptom_blob, h["text"].lower())
                # flat floor so a testing hypothesis with zero similarity
                # is still surfaced above rejected ones
                score = max(0.3, base) + evidence_bonus
                score = min(0.9, score)
            scored.append({
                "text": h["text"],
                "status": h["status"],
                "score": round(score, 4),
            })

        scored.sort(key=lambda r: r["score"], reverse=True)

        # Suggested runbooks — use the first symptom as the probe
        suggested_runbooks: List[Dict[str, Any]] = []
        match_fn = getattr(self, "runbook_match", None)
        if callable(match_fn) and symptoms:
            try:
                # touch=False so RCA is pure observation
                suggested_runbooks = match_fn(symptoms[0], limit=3, touch=False)
            except TypeError:
                suggested_runbooks = match_fn(symptoms[0], limit=3)

        report: Dict[str, Any] = {
            "incident_id": fm.get("id", incident_id),
            "strategy": strategy,
            "severity": fm.get("severity"),
            "status": fm.get("status"),
            "hypotheses": scored,
            "suggested_runbooks": suggested_runbooks,
            "evidence_count": len(evidence_lines),
            "symptom_count": len(symptoms),
        }

        if include_related:
            report["related_incidents"] = self._related_incidents(
                incident_id=incident_id,
                symptoms=symptoms,
                affected=list(fm.get("affected") or []),
                limit=max_related,
            )

        return report

    # --- helpers -----------------------------------------------------------

    def _related_incidents(
        self,
        incident_id: str,
        symptoms: List[str],
        affected: List[str],
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        sym_blob = " ".join(symptoms).lower()
        aff_set = set(affected or [])
        out: List[Dict[str, Any]] = []
        for p in list_incidents(self.base_dir):  # type: ignore[attr-defined]
            try:
                fm, heading, sections = read_doc(p)
            except Exception:
                continue
            if fm.get("type") != "incident":
                continue
            if fm.get("id") == incident_id:
                continue
            # score
            other_syms = " ".join(sections.get("Symptoms", [])).lower()
            sim = _sim(sym_blob, other_syms)
            other_aff = set(fm.get("affected") or [])
            aff_overlap = len(aff_set & other_aff)
            aff_bonus = 0.3 if aff_overlap else 0.0
            score = sim + aff_bonus
            if score < 0.2:
                continue
            out.append({
                "id": fm.get("id"),
                "title": fm.get("title"),
                "severity": fm.get("severity"),
                "status": fm.get("status"),
                "detected_at": fm.get("detected_at"),
                "score": round(score, 4),
                "affected_overlap": sorted(aff_set & other_aff),
                "path": str(p),
            })
        out.sort(key=lambda r: r["score"], reverse=True)
        return out[:limit]
