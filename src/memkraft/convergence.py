"""Convergence Check — MemKraft v0.9.2 M2 alpha.

Part of the MemKraft 1.0.0 "Empirical Memory Loop" roadmap (M2).

``convergence_check`` encodes mizchi's empirical-prompt-tuning stopping
rule as a decay-aware API call: a prompt "converges" when the last N
iterations show stable accuracy / steps / duration AND no unclear
points. If the last converged iteration is older than the tier-specific
decay cutoff, the verdict is ruled ``stale`` and a re-run is suggested.

Design principles (see ``memory/memkraft-1.0-design-proposal-2026-04-20.md``):
- Additive only. ``core.py`` is NOT modified.
- Reuses 0.9.1 ``decision_search`` + ``decision_get`` + ``tier_set``.
- Zero dependencies. Stdlib only.
- No LLM calls. All judgment is numeric.

Public API (alpha):

    mk.convergence_check(
        prompt_id,
        *,
        window=2,
        max_accuracy_delta=3.0,
        max_steps_delta_pct=10.0,
        max_duration_delta_pct=15.0,
        consider_decay=True,
        stale_after_days=None,
    ) -> dict
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .storage.incident_storage import slugify


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Default stale-after-days per tier. None values = never stale.
_TIER_STALE_DAYS: Dict[str, Optional[float]] = {
    "core": 180.0,
    "recall": 60.0,
    "archival": None,
}


_METRIC_LINE_RE = re.compile(
    r"pass_rate=(?P<pr>[\d.None]+)%?,\s*"
    r"avg_accuracy=(?P<acc>[\d.None]+),\s*"
    r"avg_tool_uses=(?P<tu>[\d.None]+),\s*"
    r"total_duration_ms=(?P<dur>[\d.None]+)"
)


def _parse_metrics_from_how(how_section: List[str]) -> Dict[str, Optional[float]]:
    """Parse the ``metrics:`` line written by ``prompt_eval``.

    Shape: ``- metrics: pass_rate=100.0%, avg_accuracy=100.0,
    avg_tool_uses=3.0, total_duration_ms=1200``.
    """
    joined = "\n".join(how_section or [])
    m = _METRIC_LINE_RE.search(joined)
    out: Dict[str, Optional[float]] = {
        "pass_rate": None,
        "avg_accuracy": None,
        "avg_tool_uses": None,
        "total_duration_ms": None,
    }
    if not m:
        return out

    def _coerce(v: str) -> Optional[float]:
        if v in ("None", "", "null"):
            return None
        try:
            return float(v)
        except ValueError:
            return None

    out["pass_rate"] = _coerce(m.group("pr"))
    out["avg_accuracy"] = _coerce(m.group("acc"))
    out["avg_tool_uses"] = _coerce(m.group("tu"))
    out["total_duration_ms"] = _coerce(m.group("dur"))
    return out


_UNCLEAR_RE = re.compile(r"unclear=(\d+)", re.IGNORECASE)


def _parse_unclear_from_title(title: str) -> int:
    m = _UNCLEAR_RE.search(title or "")
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def _parse_iter_from_tags(tags: List[str]) -> Optional[int]:
    for t in tags or []:
        if isinstance(t, str) and t.startswith("iteration:"):
            try:
                return int(t.split(":", 1)[1])
            except (ValueError, IndexError):
                return None
    return None


def _age_days(iso_ts: str) -> float:
    if not iso_ts:
        return 1e9
    try:
        s = str(iso_ts)
        if "T" in s:
            dt = datetime.fromisoformat(s.split(".")[0])
        else:
            dt = datetime.fromisoformat(s)
        delta = datetime.now() - dt.replace(tzinfo=None)
        return max(0.0, delta.total_seconds() / 86400.0)
    except Exception:
        return 1e9


def _normalise_prompt_id(prompt_id: str) -> str:
    if not prompt_id or not str(prompt_id).strip():
        raise ValueError("prompt_id must be a non-empty string")
    return slugify(str(prompt_id).strip(), max_len=80)


def _read_live_note_tier(mk: Any, slug: str) -> str:
    """Best-effort: read the tier from the prompt live-note frontmatter."""
    try:
        live_path: Path = mk.live_notes_dir / f"{slug}.md"
        if not live_path.exists():
            return "recall"
        text = live_path.read_text(encoding="utf-8")
        # very small frontmatter lookup — avoid importing yaml
        if text.startswith("---"):
            _, _, rest = text.partition("---")
            fm_text, _, _ = rest.partition("---")
            for line in fm_text.splitlines():
                if line.strip().startswith("tier:"):
                    val = line.split(":", 1)[1].strip().strip('"').strip("'")
                    return val or "recall"
    except Exception:
        pass
    return "recall"


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class ConvergenceMixin:
    """Adds ``convergence_check`` to :class:`MemKraft`."""

    def convergence_check(
        self,
        prompt_id: str,
        *,
        window: int = 2,
        max_accuracy_delta: float = 3.0,
        max_steps_delta_pct: float = 10.0,
        max_duration_delta_pct: float = 15.0,
        consider_decay: bool = True,
        stale_after_days: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Judge whether the last ``window`` iterations have converged.

        See ``memory/memkraft-0.9.2-m2-spec-2026-04-20.md`` §2 for the
        full contract.
        """
        slug = _normalise_prompt_id(prompt_id)

        live_path: Path = self.live_notes_dir / f"{slug}.md"  # type: ignore[attr-defined]
        if not live_path.exists():
            raise ValueError(
                f"prompt_id {prompt_id!r} is not registered — call "
                "mk.prompt_register(...) first"
            )

        if not isinstance(window, int) or window < 2:
            raise ValueError("window must be an int >= 2")
        if max_accuracy_delta < 0 or max_steps_delta_pct < 0 or max_duration_delta_pct < 0:
            raise ValueError("delta thresholds must be >= 0")

        # --- gather iteration decisions ------------------------------
        try:
            decisions = self.decision_search(  # type: ignore[attr-defined]
                query=None,
                tag=f"prompt:{slug}",
                limit=500,
            ) or []
        except Exception:
            decisions = []

        # annotate with iteration number
        iter_records: List[Tuple[int, Dict[str, Any]]] = []
        for d in decisions:
            n = _parse_iter_from_tags(d.get("tags") or [])
            if n is None:
                continue
            iter_records.append((n, d))

        iter_records.sort(key=lambda x: x[0], reverse=True)  # newest-first by iter #

        base_response: Dict[str, Any] = {
            "prompt_id": slug,
            "converged": False,
            "reason": "insufficient-iters",
            "window": int(window),
            "iterations_checked": [],
            "metrics": {
                "accuracy_delta": None,
                "steps_delta_pct": None,
                "duration_delta_pct": None,
                "pass_rate": None,
                "unclear_total": 0,
            },
            "last_iteration_age_days": None,
            "suggested_next": "patch-and-iterate",
        }

        if len(iter_records) < window:
            # 1.0.1: surface the iterations that *were* found so callers
            # can tell "no iters" from "1 iter, needs window=2" without
            # re-querying. Empty list previously made the two cases
            # indistinguishable.
            found_iters = [n for n, _ in iter_records]
            nxt = "first-iteration" if not found_iters else "patch-and-iterate"
            return {
                **base_response,
                "iterations_checked": found_iters,
                "suggested_next": nxt,
            }

        selected = iter_records[:window]

        # --- load metrics for selected iterations --------------------
        parsed: List[Dict[str, Any]] = []
        for iter_n, d in selected:
            try:
                detail = self.decision_get(d.get("id"))  # type: ignore[attr-defined]
            except Exception:
                return {
                    **base_response,
                    "reason": "decision-load-failed",
                    "iterations_checked": [iter_n for iter_n, _ in selected],
                    "suggested_next": "patch-and-iterate",
                }
            sections = detail.get("sections") or {}
            how = sections.get("How") or []
            metrics = _parse_metrics_from_how(how)
            title = str((detail.get("frontmatter") or {}).get("title") or "")
            unclear = _parse_unclear_from_title(title)
            parsed.append(
                {
                    "iteration": iter_n,
                    "decided_at": str((detail.get("frontmatter") or {}).get("decided_at") or ""),
                    "metrics": metrics,
                    "unclear": unclear,
                }
            )

        iter_numbers = [p["iteration"] for p in parsed]
        last_age = _age_days(parsed[0]["decided_at"])

        # --- decay gate (applied before convergence math on purpose:
        # mizchi rule says "stale verdict invalidates", so a prompt that
        # could be converged today is still reported stale if too old) -
        if consider_decay:
            if stale_after_days is None:
                tier = _read_live_note_tier(self, slug)
                stale_after_days_eff = _TIER_STALE_DAYS.get(tier)
            else:
                stale_after_days_eff = float(stale_after_days)

            if stale_after_days_eff is not None and last_age > stale_after_days_eff:
                return {
                    **base_response,
                    "reason": "stale",
                    "iterations_checked": iter_numbers,
                    "last_iteration_age_days": round(last_age, 2),
                    "metrics": {
                        "accuracy_delta": None,
                        "steps_delta_pct": None,
                        "duration_delta_pct": None,
                        "pass_rate": None,
                        "unclear_total": sum(p["unclear"] for p in parsed),
                    },
                    "suggested_next": "re-run",
                }

        # --- all iterations must have pass_rate==100 -----------------
        pass_rates = [p["metrics"].get("pass_rate") for p in parsed]
        unclear_total = sum(p["unclear"] for p in parsed)
        accuracies = [p["metrics"].get("avg_accuracy") for p in parsed if p["metrics"].get("avg_accuracy") is not None]
        steps = [p["metrics"].get("avg_tool_uses") for p in parsed if p["metrics"].get("avg_tool_uses") is not None]
        durations = [p["metrics"].get("total_duration_ms") for p in parsed if p["metrics"].get("total_duration_ms") is not None]

        def _max_abs_delta(vals: List[float]) -> Optional[float]:
            if len(vals) < 2:
                return None
            return max(vals) - min(vals)

        acc_delta = _max_abs_delta([float(x) for x in accuracies]) if accuracies else None
        steps_delta = _max_abs_delta([float(x) for x in steps]) if steps else None
        dur_delta = _max_abs_delta([float(x) for x in durations]) if durations else None

        steps_mean = (sum(steps) / len(steps)) if steps else None
        dur_mean = (sum(durations) / len(durations)) if durations else None

        steps_delta_pct = (
            (steps_delta / steps_mean * 100.0)
            if (steps_delta is not None and steps_mean not in (None, 0))
            else None
        )
        dur_delta_pct = (
            (dur_delta / dur_mean * 100.0)
            if (dur_delta is not None and dur_mean not in (None, 0))
            else None
        )

        common_metrics = {
            "accuracy_delta": round(acc_delta, 4) if acc_delta is not None else None,
            "steps_delta_pct": round(steps_delta_pct, 4) if steps_delta_pct is not None else None,
            "duration_delta_pct": round(dur_delta_pct, 4) if dur_delta_pct is not None else None,
            "pass_rate": pass_rates[0] if pass_rates else None,
            "unclear_total": unclear_total,
        }

        # --- reason classification -----------------------------------
        def _fail(reason: str, nxt: str) -> Dict[str, Any]:
            return {
                "prompt_id": slug,
                "converged": False,
                "reason": reason,
                "window": int(window),
                "iterations_checked": iter_numbers,
                "metrics": common_metrics,
                "last_iteration_age_days": round(last_age, 2),
                "suggested_next": nxt,
            }

        # Not all passed fully? mizchi's rule requires 100% success.
        if any(pr is None or pr < 100.0 for pr in pass_rates):
            return _fail("not-all-passed", "patch-and-iterate")

        if unclear_total > 0:
            return _fail("unclear-points", "patch-and-iterate")

        if acc_delta is not None and acc_delta > float(max_accuracy_delta):
            return _fail("accuracy-delta", "patch-and-iterate")

        if steps_delta_pct is not None and steps_delta_pct > float(max_steps_delta_pct):
            return _fail("steps-delta", "patch-and-iterate")

        if dur_delta_pct is not None and dur_delta_pct > float(max_duration_delta_pct):
            return _fail("duration-delta", "patch-and-iterate")

        return {
            "prompt_id": slug,
            "converged": True,
            "reason": "converged",
            "window": int(window),
            "iterations_checked": iter_numbers,
            "metrics": common_metrics,
            "last_iteration_age_days": round(last_age, 2),
            "suggested_next": "stop",
        }
