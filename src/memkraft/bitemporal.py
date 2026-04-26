"""Bitemporal Fact Layer — MemKraft v0.8.0

Tracks facts with both *valid_time* (when the fact was actually true in the
real world) and *record_time* (when we learned / recorded it). This is the
Graphiti / Memento idea, but stored transparently in Markdown so it
remains human-readable and git-diffable.

Storage format
--------------

Each entity has a file at ``memory/facts/<slug>.md``::

    # Entity: Simon

    - role: CEO of Hashed <!-- valid:[2020-03-01..) recorded:2026-04-17T00:30 -->
    - role: CTO <!-- valid:[2018-01-01..2020-02-29] recorded:2024-05-10T14:22 -->

The marker ``<!-- valid:[FROM..TO] recorded:WHEN -->`` is parsed with a
strict regex; lines without it are ignored by fact APIs (so a human can
still add freeform notes to the file).

Zero dependencies — stdlib only.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

# Matches the inline marker.  Intervals are ``[from..to]`` (closed) or
# ``[from..)`` (open-ended upper bound).  Either bound may also be empty.
_MARKER_RE = re.compile(
    r"<!--\s*valid:\[(?P<vfrom>[^.\]\)]*)\.\.(?P<vto>[^\]\)]*)[\]\)]\s+"
    r"recorded:(?P<recorded>[^\s>]+)\s*-->"
)
_LINE_RE = re.compile(
    r"^\s*-\s*(?P<key>[^:]+?):\s*(?P<value>.*?)\s*"
    r"<!--\s*valid:\[(?P<vfrom>[^.\]\)]*)\.\.(?P<vto>[^\]\)]*)[\]\)]\s+"
    r"recorded:(?P<recorded>[^\s>]+)\s*-->\s*$"
)


def _normalise_date(value: Optional[str]) -> Optional[str]:
    """Return ``value`` as an ISO-8601 string or ``None``.

    Accepts ``None``, empty string, ``"now"``, or any already-ISO-looking
    string (``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM[:SS]``).
    """
    if value is None:
        return None
    v = str(value).strip()
    if not v or v.lower() == "now" or v.lower() == "none":
        return None
    return v


def _format_interval(valid_from: Optional[str], valid_to: Optional[str]) -> str:
    vf = valid_from or ""
    vt = valid_to or ""
    # open-ended upper bound is rendered as ``[from..)`` for readability.
    if vt:
        return f"[{vf}..{vt}]"
    return f"[{vf}..)"


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def _cmp_date(a: Optional[str], b: Optional[str]) -> int:
    """Lexicographic date compare treating ``None`` as ``-inf``."""
    if a is None and b is None:
        return 0
    if a is None:
        return -1
    if b is None:
        return 1
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def parse_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a single bullet line into a fact dict or ``None``."""
    m = _LINE_RE.match(line)
    if not m:
        return None
    d = m.groupdict()
    return {
        "key": d["key"].strip(),
        "value": d["value"].strip(),
        "valid_from": d["vfrom"].strip() or None,
        "valid_to": d["vto"].strip() or None,
        "recorded_at": d["recorded"].strip(),
    }


def format_line(
    key: str,
    value: str,
    valid_from: Optional[str] = None,
    valid_to: Optional[str] = None,
    recorded_at: Optional[str] = None,
) -> str:
    interval = _format_interval(valid_from, valid_to)
    recorded = recorded_at or _now_iso()
    return f"- {key}: {value} <!-- valid:{interval} recorded:{recorded} -->"


# ---------------------------------------------------------------------------
# Main mixin
# ---------------------------------------------------------------------------


class BitemporalMixin:
    """Mixin added to :class:`MemKraft` providing the fact_* API."""

    # --- path helpers ------------------------------------------------------

    def _facts_dir(self) -> Path:
        path = self.base_dir / "facts"  # type: ignore[attr-defined]
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _fact_file(self, entity: str) -> Path:
        slug = self._slugify(entity)  # type: ignore[attr-defined]
        return self._facts_dir() / f"{slug}.md"

    def _ensure_fact_header(self, path: Path, entity: str) -> None:
        if not path.exists():
            path.write_text(f"# Entity: {entity}\n\n", encoding="utf-8")

    # --- core read ---------------------------------------------------------

    def _read_facts(self, entity: str) -> List[Dict[str, Any]]:
        path = self._fact_file(entity)
        if not path.exists():
            return []
        facts: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = parse_line(line)
            if parsed:
                facts.append(parsed)
        return facts

    # --- public API --------------------------------------------------------

    # --- internal helpers (v2.2) ------------------------------------------

    def _close_stale_facts(
        self,
        entity: str,
        key: str,
        new_valid_from: Optional[str],
        *,
        recorded_at: Optional[str] = None,
    ) -> int:
        """Auto-close any open-ended fact(s) for ``entity.key`` (v2.2).

        Sets ``valid_to`` on every existing fact for ``key`` whose
        ``valid_to`` is ``None``. The closing date is ``new_valid_from``
        when provided, otherwise today.

        Only facts with ``valid_from <= new_valid_from`` are closed; a
        future-dated existing fact is left untouched (cannot retroactively
        close something that hasn't started yet).

        Returns the number of facts modified.
        """
        path = self._fact_file(entity)
        if not path.exists():
            return 0

        close_at = _normalise_date(new_valid_from) or _now_iso().split("T")[0]
        rec = _normalise_date(recorded_at) or _now_iso()

        modified = 0
        new_lines: List[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = parse_line(line)
            should_close = (
                parsed is not None
                and parsed["key"] == key
                and parsed["valid_to"] is None
                and (
                    parsed["valid_from"] is None
                    or parsed["valid_from"] <= close_at
                )
            )
            if should_close:
                new_line = format_line(
                    parsed["key"],
                    parsed["value"],
                    parsed["valid_from"],
                    close_at,
                    rec,
                )
                new_lines.append(new_line)
                modified += 1
            else:
                new_lines.append(line)

        if modified:
            path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return modified

    def fact_add(
        self,
        entity: str,
        key: str,
        value: str,
        *,
        valid_from: Optional[str] = None,
        valid_to: Optional[str] = None,
        recorded_at: Optional[str] = None,
        auto_close_stale: bool = True,
    ) -> Dict[str, Any]:
        """Append a new fact to ``entity``'s fact file.

        Parameters are keyword-only (except the three positional ones) so we
        can add new optional parameters later without breaking callers.
        Returns the stored fact as a dict.

        v2.2 — Knowledge Update auto-detect
        ------------------------------------
        When ``auto_close_stale=True`` (default) and the new fact is
        open-ended (``valid_to is None``), any existing open-ended fact for
        the same ``entity.key`` will be automatically closed: its
        ``valid_to`` is set to the new fact's ``valid_from`` (or today, if
        ``valid_from`` is also None). This implements the
        ``role: CEO -> role: CTO`` pattern without requiring an explicit
        ``fact_invalidate`` call.

        Pass ``auto_close_stale=False`` to opt out (e.g. when backfilling
        historical facts that should coexist with the currently-open fact).
        """
        if not entity or not entity.strip():
            raise ValueError("entity must be a non-empty string")
        if not key or not key.strip():
            raise ValueError("key must be a non-empty string")
        if value is None:
            raise ValueError("value must not be None")

        vf = _normalise_date(valid_from)
        vt = _normalise_date(valid_to)
        rec = _normalise_date(recorded_at) or _now_iso()

        if vf and vt and vf > vt:
            raise ValueError(
                f"valid_from ({vf}) must be <= valid_to ({vt})"
            )

        # v2.2: auto-close any stale open-ended fact for the same key.
        # Only triggered when the new fact itself is open-ended; backfilling
        # a closed historical fact (valid_to set) should not retroactively
        # close the currently-open fact.
        if auto_close_stale and vt is None:
            self._close_stale_facts(
                entity,
                key.strip(),
                vf,
                recorded_at=rec,
            )

        path = self._fact_file(entity)
        self._ensure_fact_header(path, entity)

        line = format_line(
            key.strip(), str(value).strip(), vf, vt, rec
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

        return {
            "entity": entity,
            "key": key.strip(),
            "value": str(value).strip(),
            "valid_from": vf,
            "valid_to": vt,
            "recorded_at": rec,
        }

    def fact_at(
        self,
        entity: str,
        key: str,
        *,
        as_of: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the fact for ``entity.key`` that was valid at ``as_of``.

        If multiple facts overlap the point, the one with the latest
        ``recorded_at`` wins (i.e. the most recent belief about that time).
        Returns ``None`` if nothing valid is found.
        """
        as_of = _normalise_date(as_of) or _now_iso().split("T")[0]
        candidates = [
            f for f in self._read_facts(entity)
            if f["key"] == key
            and (f["valid_from"] is None or f["valid_from"] <= as_of)
            and (f["valid_to"] is None or as_of <= f["valid_to"])
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda f: f["recorded_at"], reverse=True)
        return candidates[0]

    def fact_history(
        self,
        entity: str,
        key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """All facts for ``entity``, sorted by ``recorded_at`` ascending.

        If ``key`` is given, filter to that key only.
        """
        facts = self._read_facts(entity)
        if key is not None:
            facts = [f for f in facts if f["key"] == key]
        facts.sort(key=lambda f: (f["recorded_at"], f["valid_from"] or ""))
        return facts

    def fact_invalidate(
        self,
        entity: str,
        key: str,
        *,
        invalid_at: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> int:
        """Close out any currently-open fact(s) for ``entity.key``.

        Rewrites the file so every open-ended fact matching ``key`` gets a
        ``valid_to`` equal to ``invalid_at`` (default: today).  Returns the
        number of facts that were modified.
        """
        path = self._fact_file(entity)
        if not path.exists():
            return 0

        invalid_at = _normalise_date(invalid_at) or _now_iso().split("T")[0]
        rec = _normalise_date(recorded_at) or _now_iso()

        modified = 0
        new_lines: List[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = parse_line(line)
            if parsed and parsed["key"] == key and parsed["valid_to"] is None:
                # close it
                new_line = format_line(
                    parsed["key"],
                    parsed["value"],
                    parsed["valid_from"],
                    invalid_at,
                    rec,
                )
                new_lines.append(new_line)
                modified += 1
            else:
                new_lines.append(line)

        if modified:
            path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return modified

    def fact_list(self, entity: str) -> List[Dict[str, Any]]:
        """All facts for entity (alias of fact_history with no key filter)."""
        return self.fact_history(entity)

    def fact_keys(self, entity: str) -> List[str]:
        """Distinct keys recorded for ``entity``, sorted."""
        return sorted({f["key"] for f in self._read_facts(entity)})
