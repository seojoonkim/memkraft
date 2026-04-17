"""memkraft stats — compute + export workspace statistics.

Output formats:
    - human (default; simple printout)
    - json (stdout or --out file)
    - csv  (flat key/value rows)

Used by CI dashboards, Slack webhooks, and manual curiosity.
Zero new dependencies — stdlib only.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .core import MemKraft

# Subdirectory → "type" label. Keep stable for dashboards.
_TYPE_DIRS = [
    "entities",
    "live-notes",
    "decisions",
    "inbox",
    "originals",
    "tasks",
    "meetings",
    "sessions",
    "debug",
]


def _iter_md(base: Path):
    """Yield all *.md files anywhere under base (excluding .memkraft/ internals)."""
    if not base.exists():
        return
    for root, dirs, files in os.walk(base):
        # skip internal .memkraft dir
        dirs[:] = [d for d in dirs if d != ".memkraft"]
        for f in files:
            if f.endswith(".md"):
                yield Path(root) / f


def _detect_tier(text: str) -> str:
    """Heuristic: look for ``tier: X`` in YAML frontmatter or `**Tier: X`` line."""
    low = text.lower()
    for t in ("core", "recall", "archival"):
        if f"tier: {t}" in low or f"tier:{t}" in low:
            return t
    return "unset"


def _count_wikilinks(text: str) -> int:
    return len(re.findall(r"\[\[([^\[\]\n]+?)\]\]", text))


def _mtime_iso(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        return ""


def collect(base_dir: str = "") -> Dict[str, Any]:
    """Gather stats for a workspace. Safe on empty/missing workspaces."""
    mk = MemKraft(base_dir=base_dir) if base_dir else MemKraft()
    base = mk.base_dir

    by_type: Dict[str, int] = {k: 0 for k in _TYPE_DIRS}
    by_tier: Dict[str, int] = {"core": 0, "recall": 0, "archival": 0, "unset": 0}
    total = 0
    oldest: Optional[str] = None
    newest: Optional[str] = None
    last_modified: Optional[str] = None
    link_edges = 0
    node_count = 0

    for md in _iter_md(base):
        total += 1
        # type from parent dir (closest match)
        try:
            rel = md.relative_to(base)
            parts = rel.parts
            if parts and parts[0] in by_type:
                by_type[parts[0]] += 1
        except ValueError:
            pass

        try:
            txt = md.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        by_tier[_detect_tier(txt)] += 1
        link_edges += _count_wikilinks(txt)

        mt = _mtime_iso(md)
        if mt:
            if oldest is None or mt < oldest:
                oldest = mt
            if newest is None or mt > newest:
                newest = mt
            if last_modified is None or mt > last_modified:
                last_modified = mt

    # node count = unique entity files (entities/ + live-notes/)
    for sub in ("entities", "live-notes"):
        p = base / sub
        if p.exists():
            node_count += sum(1 for _ in p.glob("*.md"))

    # decay stats — count files with "decay" metadata if present
    decay_active = 0
    decay_flagged = 0
    for md in _iter_md(base):
        try:
            txt = md.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        low = txt.lower()
        if "decay: flagged" in low or "⚠️ stale" in low:
            decay_flagged += 1
        else:
            decay_active += 1

    report: Dict[str, Any] = {
        "version": __version__,
        "base_dir": str(base),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_memories": total,
        "by_type": by_type,
        "by_tier": by_tier,
        "link_graph": {
            "nodes": node_count,
            "edges": link_edges,
        },
        "decay_stats": {
            "active": decay_active,
            "flagged": decay_flagged,
        },
        "bitemporal_range": {
            "oldest": oldest or "",
            "newest": newest or "",
        },
        "last_modified": last_modified or "",
    }
    return report


def _flatten(report: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flatten nested dict into rows for CSV."""
    rows: List[Dict[str, str]] = []

    def walk(prefix: str, obj: Any):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        else:
            rows.append({"key": prefix, "value": "" if obj is None else str(obj)})

    walk("", report)
    return rows


def format_csv(report: Dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["key", "value"])
    writer.writeheader()
    for row in _flatten(report):
        writer.writerow(row)
    return buf.getvalue()


def format_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)


def format_human(report: Dict[str, Any]) -> str:
    lines = []
    lines.append(f"📊 MemKraft stats — v{report['version']}")
    lines.append(f"   base_dir: {report['base_dir']}")
    lines.append(f"   total memories: {report['total_memories']}")
    lines.append("")
    lines.append("   by tier:")
    for k, v in report["by_tier"].items():
        if v:
            lines.append(f"     {k:10s} {v}")
    lines.append("")
    lines.append("   by type:")
    for k, v in report["by_type"].items():
        if v:
            lines.append(f"     {k:10s} {v}")
    lines.append("")
    lg = report["link_graph"]
    lines.append(f"   link graph: {lg['nodes']} nodes, {lg['edges']} edges")
    ds = report["decay_stats"]
    lines.append(f"   decay: {ds['active']} active, {ds['flagged']} flagged")
    br = report["bitemporal_range"]
    if br["oldest"]:
        lines.append(f"   range: {br['oldest']} → {br['newest']}")
    if report["last_modified"]:
        lines.append(f"   last modified: {report['last_modified']}")
    return "\n".join(lines)


def cmd(args) -> int:
    """argparse entry point.

    args attributes expected:
        - base_dir (str, optional)
        - export   (str or None; one of 'json'/'csv')
        - out      (str, optional output file path)
    """
    report = collect(base_dir=getattr(args, "base_dir", "") or "")
    fmt = getattr(args, "export", None) or ""
    out_path = getattr(args, "out", "") or ""

    if fmt == "json":
        payload = format_json(report)
    elif fmt == "csv":
        payload = format_csv(report)
    elif fmt in ("", "human", None):
        payload = format_human(report)
    else:
        print(f"❌ unknown --export format: {fmt} (use json|csv)")
        return 2

    if out_path:
        try:
            Path(out_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).expanduser().write_text(payload, encoding="utf-8")
            print(f"✅ stats written to {out_path}")
        except Exception as e:
            print(f"❌ failed to write {out_path}: {e}")
            return 1
    else:
        print(payload)
    return 0
