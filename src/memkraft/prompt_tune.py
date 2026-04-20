"""Prompt Tune Layer — MemKraft v0.9.2 (M1 alpha)

First milestone of the MemKraft 1.0.0 "Empirical Memory Loop" roadmap.

Makes a prompt/skill a **first-class MemKraft entity** and records every
empirical tuning iteration as a decision (plus an incident when unclear
points pile up).

Design principles (from ``memory/memkraft-1.0-design-proposal-2026-04-20.md``):
- No LLM calls inside MemKraft. The host agent dispatches the evaluation
  subagent; MemKraft only records the report it was handed.
- Additive only — core.py is not modified. We expose methods via
  :class:`PromptTuneMixin` and the package ``__init__`` composes them onto
  :class:`MemKraft` using the same attach pattern v0.8.0+ uses.
- Every new storage primitive reuses existing mixins (``track``/``update``
  for entity persistence, ``decision_record``/``incident_record`` for
  iteration events, ``link_scan`` for the dependency graph, ``tier_set``
  for retention tier).
- Zero new dependencies. Stdlib only.

Public API (alpha):

    mk.prompt_register(prompt_id, path, owner, *, tags=None,
                       critical_requirements=None, description="") -> dict
    mk.prompt_eval(prompt_id, iteration, scenarios, results,
                   *, models_used=None, applied_patch=None,
                   applied_reason=None) -> dict

Returned dicts are JSON-serialisable so callers can round-trip through
transcripts, subagent reports, and log pipelines.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage.incident_storage import now_iso, slugify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_UNCLEAR_POINTS_INCIDENT_THRESHOLD = 3
"""If an iteration surfaces this many (or more) unclear points we
automatically open an incident so the host agent can triage it.

Kept as a module constant so tests and future milestones can tune it
without touching callers.
"""


def _normalise_prompt_id(prompt_id: str) -> str:
    """Return a deterministic slug-style id for the prompt entity.

    The caller may pass either ``"prompt-guard"`` or a path-flavoured id
    such as ``"skills/prompt-guard"``; both collapse to a single slug so
    ``track`` / ``update`` always hit the same live-note file.
    """
    if not prompt_id or not str(prompt_id).strip():
        raise ValueError("prompt_id must be a non-empty string")
    return slugify(str(prompt_id).strip(), max_len=80)


def _require_registered(mk: Any, prompt_id: str) -> Path:
    """Return the live-note path for ``prompt_id`` or raise if missing."""
    slug = _normalise_prompt_id(prompt_id)
    path = mk.live_notes_dir / f"{slug}.md"
    if not path.exists():
        raise ValueError(
            f"prompt_id {prompt_id!r} is not registered — call "
            "mk.prompt_register(...) first"
        )
    return path


def _summarise_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Condense the iteration report into stats used by decision/incident."""
    if not results:
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "pass_rate": None,
            "avg_accuracy": None,
            "total_tool_uses": 0,
            "avg_tool_uses": None,
            "total_duration_ms": 0,
            "unclear_count": 0,
            "unclear_points": [],
        }

    total = len(results)
    passed = sum(1 for r in results if r.get("success"))
    failed = total - passed
    accuracies = [r.get("accuracy") for r in results if isinstance(r.get("accuracy"), (int, float))]
    tool_uses = [r.get("tool_uses") for r in results if isinstance(r.get("tool_uses"), (int, float))]
    durations = [r.get("duration_ms") for r in results if isinstance(r.get("duration_ms"), (int, float))]
    unclear: List[str] = []
    for r in results:
        for u in r.get("unclear_points", []) or []:
            if isinstance(u, str):
                unclear.append(u)
            elif isinstance(u, dict):
                # accept {"item": "..."} shape too
                label = u.get("item") or u.get("description") or str(u)
                unclear.append(str(label))
    avg_acc = round(sum(accuracies) / len(accuracies), 2) if accuracies else None
    avg_tools = round(sum(tool_uses) / len(tool_uses), 2) if tool_uses else None
    pass_rate = round(passed / total * 100, 1) if total else None
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": pass_rate,
        "avg_accuracy": avg_acc,
        "total_tool_uses": int(sum(tool_uses)) if tool_uses else 0,
        "avg_tool_uses": avg_tools,
        "total_duration_ms": int(sum(durations)) if durations else 0,
        "unclear_count": len(unclear),
        "unclear_points": unclear,
    }


def _read_timeline_ids(text: str, prefix: str) -> List[str]:
    """Return ids starting with ``prefix`` in the order they appear in the
    *Recent Activity* section of the live-note body.

    ``track``/``update`` prepend the newest entry to ``## Recent Activity``
    and also append it to ``## Timeline (Full Record)`` at the bottom.  To
    get a deterministic "most-recent-first" ordering we walk Recent
    Activity top-to-bottom — the first id we see is the newest.
    """
    out: List[str] = []
    in_recent = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_recent = stripped.startswith("## Recent Activity") or stripped.startswith(
                "## 最近動向"
            )
            continue
        if not in_recent:
            continue
        if prefix in stripped:
            for token in stripped.replace("[", " ").replace("]", " ").split():
                tok = token.strip(" ,;:()`*.")
                if tok.startswith(prefix) and tok not in out:
                    out.append(tok)
    return out


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class PromptTuneMixin:
    """Adds ``prompt_register`` + ``prompt_eval`` to :class:`MemKraft`."""

    # --- API: register -----------------------------------------------------

    def prompt_register(
        self,
        prompt_id: str,
        path: str,
        *,
        owner: str = "",
        tags: Optional[List[str]] = None,
        critical_requirements: Optional[List[str]] = None,
        description: str = "",
        validate_path: bool = False,
    ) -> Dict[str, Any]:
        """Register a prompt/skill as a first-class MemKraft entity.

        Creates a live-note under ``<base_dir>/live-notes/`` via the
        existing :meth:`track` API (``entity_type="prompt"``) and seeds it
        with the path, owner, tags, and the list of ``[critical]``
        requirements that subsequent evaluations must satisfy.

        Idempotency: calling ``prompt_register`` twice with the same
        ``prompt_id`` raises :class:`ValueError`. Use
        ``mk.update(prompt_id, ...)`` or future ``prompt_update`` APIs to
        edit an existing registration.

        Parameters
        ----------
        validate_path : bool, default False
            If True, raise :class:`FileNotFoundError` when ``path``
            does not resolve to an existing file. When False (default,
            1.0 behaviour) a warning is emitted via
            :mod:`warnings` so the caller can still register forward-
            referenced skills without breaking change.

        Returns
        -------
        dict
            ``{"prompt_id", "slug", "entity_path", "registered_at", "tier",
               "tags", "critical_requirements", "owner", "path"}``
        """
        slug = _normalise_prompt_id(prompt_id)
        live_path: Path = self.live_notes_dir / f"{slug}.md"  # type: ignore[attr-defined]
        if live_path.exists():
            raise ValueError(
                f"prompt_id {prompt_id!r} is already registered at {live_path}"
            )

        if not path or not str(path).strip():
            raise ValueError("path must be a non-empty string")
        path_str = str(path).strip()

        # 1.0.1: path validation — opt-in to preserve backward compat.
        # Still warn by default so silent typos surface in logs.
        if not Path(path_str).expanduser().exists():
            if validate_path:
                raise FileNotFoundError(
                    f"prompt path does not exist: {path_str}"
                )
            import warnings as _w
            _w.warn(
                f"prompt path does not exist: {path_str!r} "
                f"(registering anyway; pass validate_path=True to enforce)",
                stacklevel=2,
            )

        tag_list = list(tags or [])
        crit_list = [str(x).strip() for x in (critical_requirements or []) if str(x).strip()]
        owner_str = (owner or "").strip()
        desc = (description or "").strip()

        # --- create the entity via existing ``track`` ------------------
        source = f"prompt_register:{owner_str}" if owner_str else "prompt_register"
        created = self.track(slug, entity_type="prompt", source=source)  # type: ignore[attr-defined]
        if created is None or not live_path.exists():
            raise RuntimeError(
                f"failed to create prompt entity for {prompt_id!r}: "
                "track() returned no file"
            )

        # --- seed the body with prompt-specific metadata ---------------
        now = now_iso()
        text = live_path.read_text(encoding="utf-8")

        seed_block_lines = [
            "## Prompt Metadata",
            f"- **prompt_id**: `{slug}`",
            f"- **path**: `{path_str}`",
            f"- **owner**: {owner_str or '(unspecified)'}",
            f"- **tags**: {', '.join(tag_list) if tag_list else '(none)'}",
            f"- **registered_at**: {now}",
        ]
        if desc:
            seed_block_lines.append(f"- **description**: {desc}")
        seed_block_lines.append("")
        if crit_list:
            seed_block_lines.append("## Critical Requirements")
            for item in crit_list:
                seed_block_lines.append(f"- [critical] {item}")
            seed_block_lines.append("")
        seed_block_lines.append("## Iterations")
        seed_block_lines.append("(populated by `mk.prompt_eval`)")
        seed_block_lines.append("")

        seed = "\n".join(seed_block_lines)
        # insert the seed right before ``## Related Entities`` so the
        # existing live-note layout stays intact.
        anchor = "## Related Entities"
        if anchor in text:
            text = text.replace(anchor, seed + anchor, 1)
        else:
            text = text.rstrip() + "\n\n" + seed
        live_path.write_text(text, encoding="utf-8")

        # --- classify as ``recall`` tier by default --------------------
        try:
            self.tier_set(slug, tier="recall")  # type: ignore[attr-defined]
        except Exception:
            # tier_set is best-effort; if the live-note structure is
            # incompatible we still return the registration info.
            pass

        # --- rebuild the link graph so [[wiki-links]] in the seed are
        # indexed alongside the rest of the memory --------------------
        try:
            self.link_scan()  # type: ignore[attr-defined]
        except Exception:
            pass

        # --- append to 'Recent Activity' so timeline reads cleanly -----
        try:
            self.update(  # type: ignore[attr-defined]
                slug,
                f"Registered prompt at `{path_str}` (owner={owner_str or 'n/a'}; "
                f"{len(crit_list)} critical requirement(s))",
                source="prompt_register",
            )
        except Exception:
            pass

        return {
            "prompt_id": slug,
            "slug": slug,
            "entity_path": str(live_path),
            "registered_at": now,
            "tier": "recall",
            "tags": tag_list,
            "critical_requirements": crit_list,
            "owner": owner_str,
            "path": path_str,
        }

    # --- API: eval ---------------------------------------------------------

    def prompt_eval(
        self,
        prompt_id: str,
        iteration: int,
        scenarios: List[Dict[str, Any]],
        results: List[Dict[str, Any]],
        *,
        models_used: Optional[List[str]] = None,
        applied_patch: Optional[str] = None,
        applied_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a single empirical tuning iteration.

        Writes:
        * a ``decision_record`` that captures *what was tried*, *why* and
          *how* the host agent dispatched it;
        * an optional ``incident_record`` when the iteration surfaced
          ``>= _UNCLEAR_POINTS_INCIDENT_THRESHOLD`` unclear points;
        * an ``Iterations`` section entry on the prompt live-note, linked
          to the previous iteration via wiki-links for graph traversal.

        The method never calls an LLM. ``scenarios``/``results`` are
        whatever the host agent's EPT subagent produced — we only persist
        it, in full fidelity, as memory.

        Parameters
        ----------
        prompt_id : str
            The id returned by :meth:`prompt_register`. Must already be
            registered.
        iteration : int
            1-based iteration index (``1``, ``2``, ``3``…).
        scenarios : list of dict
            Each scenario dict should at least carry ``name``. Optional
            keys: ``description`` and ``requirements`` (list of
            ``{"item": str, "critical": bool}``).
        results : list of dict
            Parallel per-scenario records. Recognised keys:
            ``scenario``, ``success`` (bool), ``accuracy`` (0–100),
            ``tool_uses`` (int), ``duration_ms`` (int), ``unclear_points``
            (list of str), ``discretion`` (list of str).
        models_used : list of str, optional
            Models used for this iteration. Recorded on the decision.
        applied_patch : str, optional
            Minimal diff applied *before* this iteration (from the
            previous iteration's unclear points). Stored in the decision
            *How* section.
        applied_reason : str, optional
            Why that patch was applied. Stored in the decision *Why*
            section.

        Returns
        -------
        dict
            ``{"prompt_id", "iteration", "decision_id", "incident_id"
               (None if no incident), "summary", "eval_id", "recorded_at",
               "previous_iteration_decision_id"}``
        """
        live_path = _require_registered(self, prompt_id)
        slug = live_path.stem

        if not isinstance(iteration, int) or iteration < 1:
            raise ValueError("iteration must be a positive int (>=1)")

        scenarios = list(scenarios or [])
        results = list(results or [])

        # 1.0.1: reject empty iteration — recording a decision with 0
        # scenarios and 0 results produces no analytical signal and only
        # pollutes the ledger. Callers that want "placeholder" runs
        # should record a decision directly via ``decision_record``.
        if not scenarios and not results:
            raise ValueError(
                "prompt_eval requires at least one scenario or result; "
                "got empty scenarios and results. "
                "Use mk.decision_record(...) directly to log placeholder events."
            )

        # 1.0.1: warn on scenario/result name mismatch — silent mismatch
        # previously allowed typos to persist as bad data in the ledger.
        scenario_names = {
            str(sc.get("name") or sc.get("scenario") or "").strip()
            for sc in scenarios
            if isinstance(sc, dict)
        }
        scenario_names.discard("")
        if scenario_names:
            orphan_results = [
                str(r.get("scenario") or "").strip()
                for r in results
                if isinstance(r, dict)
                and str(r.get("scenario") or "").strip()
                and str(r.get("scenario") or "").strip() not in scenario_names
            ]
            if orphan_results:
                import warnings as _w
                _w.warn(
                    f"prompt_eval: result(s) reference undeclared scenarios "
                    f"{sorted(set(orphan_results))!r}; declared scenarios are "
                    f"{sorted(scenario_names)!r}. Recording anyway.",
                    stacklevel=2,
                )

        summary = _summarise_results(results)
        recorded_at = now_iso()
        eval_id = f"eval-{slug}-iter-{iteration:03d}"

        # --- locate the previous iteration decision (for linking) ------
        text = live_path.read_text(encoding="utf-8")
        prior_decisions = _read_timeline_ids(text, "dec-")
        # Recent Activity is prepended, so index 0 is the newest.
        previous_decision = prior_decisions[0] if prior_decisions else None

        # --- compose decision_record content ---------------------------
        scenario_labels = []
        for sc in scenarios:
            if isinstance(sc, dict):
                label = sc.get("name") or sc.get("scenario") or "(unnamed)"
                scenario_labels.append(str(label))
            else:
                scenario_labels.append(str(sc))

        what = (
            f"[prompt-eval] {slug} iteration {iteration} "
            f"({summary['passed']}/{summary['total']} passed, "
            f"unclear={summary['unclear_count']})"
        )
        why_parts: List[str] = []
        if applied_reason:
            why_parts.append(applied_reason.strip())
        if previous_decision:
            why_parts.append(f"Follow-up on previous iteration: [[{previous_decision}]]")
        if summary["unclear_points"]:
            why_parts.append(
                "Open unclear points from this iteration:\n"
                + "\n".join(f"  - {u}" for u in summary["unclear_points"])
            )
        if not why_parts:
            why_parts.append(
                "Regular empirical iteration — no upstream patch, "
                "recording baseline behaviour."
            )
        why = "\n\n".join(why_parts)

        how_parts: List[str] = []
        how_parts.append(f"- eval_id: `{eval_id}`")
        how_parts.append(f"- prompt: [[{slug}]]")
        how_parts.append(f"- iteration: {iteration}")
        if models_used:
            how_parts.append(f"- models: {', '.join(models_used)}")
        how_parts.append(f"- scenarios: {', '.join(scenario_labels) if scenario_labels else '(none)'}")
        how_parts.append(
            f"- metrics: pass_rate={summary['pass_rate']}%, "
            f"avg_accuracy={summary['avg_accuracy']}, "
            f"avg_tool_uses={summary['avg_tool_uses']}, "
            f"total_duration_ms={summary['total_duration_ms']}"
        )
        if applied_patch:
            how_parts.append("- applied_patch:")
            for line in applied_patch.strip().splitlines():
                how_parts.append(f"    {line}")
        how = "\n".join(how_parts)

        outcome = (
            f"{summary['passed']}/{summary['total']} scenarios passed "
            f"(pass_rate={summary['pass_rate']}%)."
        )

        decision_tags = ["prompt-eval", f"prompt:{slug}", f"iteration:{iteration}"]

        decision_id = self.decision_record(  # type: ignore[attr-defined]
            what=what,
            why=why,
            how=how,
            outcome=outcome,
            tags=decision_tags,
            status="accepted",
            decided_at=recorded_at,
            source=f"prompt_eval:{slug}",
            tier="recall",
        )

        # --- open an incident if too many unclear points ---------------
        incident_id: Optional[str] = None
        if summary["unclear_count"] >= _UNCLEAR_POINTS_INCIDENT_THRESHOLD:
            try:
                symptoms = list(summary["unclear_points"])
                hypothesis = [
                    "Prompt/skill body does not address these edge cases",
                    "Critical requirements list may be incomplete",
                ]
                severity = (
                    "high"
                    if summary["unclear_count"] >= 2 * _UNCLEAR_POINTS_INCIDENT_THRESHOLD
                    else "medium"
                )
                incident_id = self.incident_record(  # type: ignore[attr-defined]
                    title=f"[prompt-eval] {slug} iter {iteration} — {summary['unclear_count']} unclear point(s)",
                    symptoms=symptoms,
                    hypothesis=hypothesis,
                    severity=severity,
                    affected=[slug],
                    detected_at=recorded_at,
                    source=f"prompt_eval:{slug}",
                    tags=["prompt-eval", f"prompt:{slug}"],
                )
                try:
                    self.decision_link(decision_id, incident_id)  # type: ignore[attr-defined]
                except Exception:
                    pass
            except Exception:
                # Incident creation is best-effort; the decision is the
                # primary artefact and must not be lost.
                incident_id = None

        # --- append iteration line to the prompt live-note -------------
        try:
            self.update(  # type: ignore[attr-defined]
                slug,
                (
                    f"Iter {iteration}: [[{decision_id}]] — "
                    f"{summary['passed']}/{summary['total']} passed, "
                    f"unclear={summary['unclear_count']}"
                    + (f", incident=[[{incident_id}]]" if incident_id else "")
                ),
                source="prompt_eval",
            )
        except Exception:
            pass

        # --- refresh link graph so new [[wiki-links]] are indexed ------
        try:
            self.link_scan()  # type: ignore[attr-defined]
        except Exception:
            pass

        return {
            "prompt_id": slug,
            "iteration": iteration,
            "eval_id": eval_id,
            "decision_id": decision_id,
            "incident_id": incident_id,
            "previous_iteration_decision_id": previous_decision,
            "summary": summary,
            "recorded_at": recorded_at,
            "models_used": list(models_used or []),
            "scenarios": scenario_labels,
        }
