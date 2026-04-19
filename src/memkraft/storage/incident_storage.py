"""Incident/Runbook MD file storage — MemKraft v0.9.0

Shared low-level read/write helpers for the Incident Memory Layer.

Design:
* Each incident is one MD file at ``memory/incidents/<id>.md``.
* Each runbook is one MD file at ``memory/runbooks/<id>.md``.
* YAML-ish frontmatter (parsed by the same helpers v0.8 tiers/decay use)
  stores structured metadata. Body sections hold human-readable content.

Zero dependencies — stdlib only.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..decay import _parse_frontmatter, _write_frontmatter


# ---------------------------------------------------------------------------
# List-field helpers
#
# MemKraft's v0.8 frontmatter parser only round-trips scalars. To keep the
# incident schema (which has several list-valued fields: affected, tags,
# symptoms, hypotheses_accepted, hypotheses_rejected, source_incidents, ...)
# encodable on disk we JSON-encode lists into strings in frontmatter and
# decode on read. A field ending in ``_json`` means "JSON-encoded list".
# ---------------------------------------------------------------------------

LIST_FIELDS = {
    "affected",
    "tags",
    "source_incidents",
}


def encode_list_fields(fm: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``fm`` with list values JSON-encoded to strings."""
    out: Dict[str, Any] = {}
    for k, v in fm.items():
        if isinstance(v, list):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    return out


def decode_list_fields(fm: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``fm`` with known list-fields JSON-decoded."""
    out: Dict[str, Any] = {}
    for k, v in fm.items():
        if k in LIST_FIELDS and isinstance(v, str) and v.startswith("["):
            try:
                out[k] = json.loads(v)
                continue
            except Exception:
                pass
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# Slug + id helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9가-힣]+")


def slugify(text: str, max_len: int = 60) -> str:
    """Lowercase-ish slug. Keeps Hangul. Collapses everything else to ``-``."""
    t = (text or "").strip().lower()
    t = _SLUG_RE.sub("-", t).strip("-")
    if not t:
        t = "untitled"
    return t[:max_len]


def today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def make_incident_id(title: str, detected_at: Optional[str] = None) -> str:
    date = (detected_at or today_iso())[:10]
    slug = slugify(title)
    return f"inc-{date}-{slug}"


def make_runbook_id(pattern: str) -> str:
    return f"rb-{slugify(pattern, max_len=50)}"


# ---------------------------------------------------------------------------
# Section parsing / writing
# ---------------------------------------------------------------------------

# Body sections are ``## Title`` markdown headings. We round-trip them as
# a dict of {title: [lines...]} so updates keep unrelated sections intact.

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")


def split_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Return (frontmatter_dict, body_text)."""
    fm = _parse_frontmatter(text)
    # strip the YAML block from text
    if text.startswith("---"):
        # find closing ---
        lines = text.split("\n")
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            body = "\n".join(lines[end + 1 :])
            # trim leading blank lines
            return fm, body.lstrip("\n")
    return fm, text


def parse_sections(body: str) -> Dict[str, List[str]]:
    """Parse ``## Heading`` sections into a dict.

    Preserves content before the first heading under key ``""`` (preamble).
    Lines include trailing whitespace stripped but blank lines kept for
    round-tripping.
    """
    sections: Dict[str, List[str]] = {"": []}
    current = ""
    for line in body.split("\n"):
        m = _SECTION_RE.match(line)
        if m:
            current = m.group(1).strip()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    # strip trailing blanks per section
    for k, v in list(sections.items()):
        while v and not v[-1].strip():
            v.pop()
    return sections


def render_sections(sections: Dict[str, List[str]], heading: Optional[str] = None) -> str:
    """Opposite of parse_sections."""
    parts: List[str] = []
    if heading:
        parts.append(f"# {heading}")
        parts.append("")
    preamble = sections.get("", [])
    if preamble:
        parts.extend(preamble)
        parts.append("")
    for name, lines in sections.items():
        if name == "":
            continue
        parts.append(f"## {name}")
        if lines:
            parts.extend(lines)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def write_doc(
    path: Path,
    frontmatter: Dict[str, Any],
    heading: str,
    sections: Dict[str, List[str]],
) -> None:
    """Write a complete incident/runbook MD file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = render_sections(sections, heading=heading)
    # prepend frontmatter using the helper from decay.py
    encoded = encode_list_fields(frontmatter)
    final = _write_frontmatter(body, encoded)
    path.write_text(final, encoding="utf-8")


def read_doc(path: Path) -> Tuple[Dict[str, Any], str, Dict[str, List[str]]]:
    """Return (frontmatter, heading, sections)."""
    text = path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(text)
    fm = decode_list_fields(fm)
    heading = ""
    lines = body.split("\n")
    # first ``# heading`` line
    for i, line in enumerate(lines):
        m = re.match(r"^#\s+(.+?)\s*$", line)
        if m:
            heading = m.group(1).strip()
            # rebuild body without that heading + following blank
            rest = lines[i + 1 :]
            while rest and not rest[0].strip():
                rest.pop(0)
            body = "\n".join(rest)
            break
    sections = parse_sections(body)
    return fm, heading, sections


# ---------------------------------------------------------------------------
# Directory resolution (accept any MemKraft instance duck-typed)
# ---------------------------------------------------------------------------


def incidents_dir(base_dir: Path) -> Path:
    p = base_dir / "incidents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def runbooks_dir(base_dir: Path) -> Path:
    p = base_dir / "runbooks"
    p.mkdir(parents=True, exist_ok=True)
    return p


def incident_path(base_dir: Path, incident_id: str) -> Path:
    return incidents_dir(base_dir) / f"{incident_id}.md"


def runbook_path(base_dir: Path, runbook_id: str) -> Path:
    return runbooks_dir(base_dir) / f"{runbook_id}.md"


def list_incidents(base_dir: Path) -> List[Path]:
    d = incidents_dir(base_dir)
    return sorted(p for p in d.glob("*.md") if p.name != "_index.md")


def list_runbooks(base_dir: Path) -> List[Path]:
    d = runbooks_dir(base_dir)
    return sorted(p for p in d.glob("*.md") if p.name != "_index.md")
