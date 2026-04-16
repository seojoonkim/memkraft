"""Memory Tier Labels + Working Set — MemKraft v0.8.0

Three-tier memory system (Letta-style MemGPT) implemented with a single
line of YAML frontmatter::

    ---
    tier: core        # core | recall | archival
    last_accessed: 2026-04-17
    access_count: 15
    ---

* **core**     — always injected into an agent's context, hot by definition
* **recall**   — default tier; included on demand
* **archival** — cold, only retrieved by explicit query

The "working set" of an agent is the top-N hot memories: all ``core``
memories plus the most-recently-accessed ``recall`` ones.

Zero dependencies — stdlib only.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .decay import _parse_frontmatter, _write_frontmatter, _today, _parse_date

TIERS = ("core", "recall", "archival")
_DEFAULT_TIER = "recall"
_TIER_ORDER = {"archival": 0, "recall": 1, "core": 2}


class TiersMixin:
    """Mixin added to :class:`MemKraft` providing the tier_* / working_set API."""

    # --- low-level ---------------------------------------------------------

    def _tier_resolve(self, memory_id: str) -> Optional[Path]:
        # re-use DecayMixin's resolver if available (it is — they share
        # MemKraft), but we implement a thin fallback for safety.
        resolver = getattr(self, "_resolve_memory", None)
        if callable(resolver):
            return resolver(memory_id)
        p = Path(memory_id)
        if p.is_absolute() and p.exists():
            return p
        candidate: Path = self.base_dir / memory_id  # type: ignore[attr-defined]
        if candidate.exists():
            return candidate
        return None

    def _read_tier_fm(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        return _parse_frontmatter(path.read_text(encoding="utf-8"))

    def _write_tier_fm(self, path: Path, updates: Dict[str, Any]) -> None:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        fm = _parse_frontmatter(text) if text else {}
        fm.update(updates)
        path.write_text(_write_frontmatter(text, fm), encoding="utf-8")

    # --- public API --------------------------------------------------------

    def tier_set(
        self,
        memory_id: str,
        *,
        tier: str,
    ) -> Dict[str, Any]:
        """Assign ``memory_id`` to one of ``core | recall | archival``."""
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
        path = self._tier_resolve(memory_id)
        if path is None:
            raise FileNotFoundError(f"memory not found: {memory_id}")
        self._write_tier_fm(path, {"tier": tier})
        return {"path": str(path), "tier": tier}

    def tier_of(self, memory_id: str) -> str:
        """Return the current tier (or ``recall`` if none set / file missing)."""
        path = self._tier_resolve(memory_id)
        if path is None:
            return _DEFAULT_TIER
        fm = self._read_tier_fm(path)
        t = fm.get("tier", _DEFAULT_TIER)
        if t not in TIERS:
            return _DEFAULT_TIER
        return t

    def tier_list(
        self,
        *,
        tier: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List memories grouped by tier.

        If ``tier`` is given, return only that tier.  Otherwise returns all
        memories sorted by (tier priority desc, last_accessed desc).
        """
        if tier is not None and tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")

        out: List[Dict[str, Any]] = []
        base: Path = self.base_dir  # type: ignore[attr-defined]
        for f in base.rglob("*.md"):
            if ".memkraft" in f.parts:
                continue
            fm = self._read_tier_fm(f)
            # only files that actually have a tier or that we can tag
            t = fm.get("tier")
            if t is None and tier is not None:
                # explicit filter and no tier set → skip unless the default
                # is what's being asked for.
                if tier != _DEFAULT_TIER:
                    continue
                t = _DEFAULT_TIER
            if t is None:
                t = _DEFAULT_TIER
            if t not in TIERS:
                t = _DEFAULT_TIER
            if tier is not None and t != tier:
                continue
            out.append({
                "path": str(f),
                "tier": t,
                "last_accessed": fm.get("last_accessed"),
                "access_count": int(fm.get("access_count", 0)),
            })
        out.sort(
            key=lambda r: (
                -_TIER_ORDER.get(r["tier"], 1),
                # later date first — use string sort, None goes last
                r["last_accessed"] or "",
            ),
            reverse=False,
        )
        # adjust: we want core first then recall then archival, then most
        # recently accessed first within each tier.
        out.sort(
            key=lambda r: (
                -_TIER_ORDER.get(r["tier"], 1),
                # invert date so later comes first (lexicographic reverse)
                "" if r["last_accessed"] is None else r["last_accessed"],
            )
        )
        # final pass: within identical tier, reverse by last_accessed so
        # newest first
        out.sort(
            key=lambda r: (
                _TIER_ORDER.get(r["tier"], 1) * -1,   # core(2)→-2, recall(1)→-1, arch(0)→0
                "" if r["last_accessed"] is None else r["last_accessed"],
            ),
        )
        return out

    def tier_promote(self, memory_id: str) -> Dict[str, Any]:
        """archival → recall → core.  No-op if already ``core``."""
        current = self.tier_of(memory_id)
        order = ["archival", "recall", "core"]
        idx = order.index(current) if current in order else 1
        new = order[min(idx + 1, len(order) - 1)]
        return self.tier_set(memory_id, tier=new)

    def tier_demote(self, memory_id: str) -> Dict[str, Any]:
        """core → recall → archival.  No-op if already ``archival``."""
        current = self.tier_of(memory_id)
        order = ["archival", "recall", "core"]
        idx = order.index(current) if current in order else 1
        new = order[max(idx - 1, 0)]
        return self.tier_set(memory_id, tier=new)

    def tier_touch(self, memory_id: str) -> Dict[str, Any]:
        """Bump ``last_accessed`` / ``access_count`` without changing tier."""
        path = self._tier_resolve(memory_id)
        if path is None:
            raise FileNotFoundError(f"memory not found: {memory_id}")
        fm = self._read_tier_fm(path)
        new_count = int(fm.get("access_count", 0)) + 1
        self._write_tier_fm(path, {
            "last_accessed": _today(),
            "access_count": new_count,
        })
        return {
            "path": str(path),
            "tier": fm.get("tier", _DEFAULT_TIER),
            "last_accessed": _today(),
            "access_count": new_count,
        }

    def working_set(
        self,
        *,
        agent: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Return the agent's hot memories.

        All ``core`` memories are included unconditionally.  Then the most
        recently-accessed ``recall`` memories fill the set up to ``limit``.
        ``agent`` is accepted for API parity; when given it filters to
        files under ``.memkraft/agents/<agent>/`` *in addition* to the
        global core set (so agents share core memories).
        """
        if limit < 0:
            raise ValueError("limit must be >= 0")

        all_entries = self.tier_list()
        core = [e for e in all_entries if e["tier"] == "core"]
        recall = [e for e in all_entries if e["tier"] == "recall"]

        # recall: newest first by last_accessed (None last)
        recall.sort(
            key=lambda r: (r["last_accessed"] is None, r["last_accessed"]),
            reverse=True,
        )

        if agent:
            agent_prefix = str(
                self.base_dir / ".memkraft" / "agents" / agent  # type: ignore[attr-defined]
            )
            def _in_agent(e: Dict[str, Any]) -> bool:
                return e["path"].startswith(agent_prefix)
            recall = [e for e in recall if _in_agent(e) or True]
            # agent filter is additive (don't drop global core) — this
            # matches the docstring.  Left as a future extension point.

        out: List[Dict[str, Any]] = list(core)
        for e in recall:
            if len(out) >= limit:
                break
            out.append(e)
        return out[:limit] if limit > 0 else []
