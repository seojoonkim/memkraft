"""Project synchronization guard for local development checkouts.

This module records repository drift in MemKraft and applies only safe
fast-forward synchronization.  It is intentionally conservative: dirty,
untracked, missing-upstream, or diverged repositories are reported and logged,
never overwritten.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import json

SKIP_DIR_NAMES = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "node_modules",
    "site-packages",
    "vendor",
}

_EVENT_STORE = ".version-drift/events.jsonl"


@dataclass(frozen=True)
class GitResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_git(repo: Path, args: Sequence[str], timeout_s: float = 20.0) -> GitResult:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitResult(ok=False, stderr=str(exc), returncode=124)
    return GitResult(
        ok=proc.returncode == 0,
        stdout=proc.stdout.strip(),
        stderr=proc.stderr.strip(),
        returncode=proc.returncode,
    )


def _first_line(text: str) -> str:
    return (text.splitlines() or [""])[0]


def default_roots() -> List[str]:
    env = os.environ.get("MEMKRAFT_PROJECT_SYNC_ROOTS", "").strip()
    if env:
        parts = [part.strip() for part in env.split(os.pathsep) if part.strip()]
        return parts

    cwd = Path.cwd().resolve()
    candidates = [
        cwd.parent / "sano-workspace",
        cwd.parent / "memcraft",
        cwd,
    ]
    roots: List[str] = []
    for path in candidates:
        try:
            if path.exists():
                roots.append(str(path))
        except OSError:
            continue
    # Preserve order, remove duplicates.
    seen = set()
    deduped: List[str] = []
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        deduped.append(root)
    return deduped


def discover_projects(roots: Iterable[str], max_depth: int = 5) -> List[Path]:
    """Find Git worktrees under roots, skipping dependency/cache directories."""
    found: List[Path] = []
    seen: set = set()
    for raw_root in roots:
        if not raw_root:
            continue
        root = Path(raw_root).expanduser().resolve()
        if not root.exists():
            continue
        stack: List[Tuple[Path, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                real = current.resolve()
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)
            if (current / ".git").exists():
                found.append(current)
            if depth >= max_depth:
                continue
            try:
                children = sorted(current.iterdir(), key=lambda p: p.name)
            except OSError:
                continue
            for child in reversed(children):
                if not child.is_dir():
                    continue
                if child.name in SKIP_DIR_NAMES:
                    continue
                if child.name.startswith(".") and child.name not in {".config"}:
                    continue
                stack.append((child, depth + 1))
    return sorted(found, key=lambda p: str(p))


def inspect_project(path: str, fetch: bool = False) -> Dict[str, Any]:
    """Return a structured drift report for one Git project."""
    repo = Path(path).expanduser().resolve()
    report: Dict[str, Any] = {
        "schema": "version-drift/1",
        "checked_at": _now(),
        "path": str(repo),
        "exists": repo.exists(),
        "is_git": False,
        "ok": False,
        "state": "missing",
        "reasons": [],
        "actions": [],
    }
    if not repo.exists():
        report["reasons"].append("path_missing")
        return report

    top = _run_git(repo, ["rev-parse", "--show-toplevel"])
    if not top.ok:
        report["state"] = "not_git"
        report["reasons"].append("not_git")
        report["error"] = top.stderr
        return report

    repo = Path(top.stdout).resolve()
    report["path"] = str(repo)
    report["is_git"] = True

    if fetch:
        fetch_result = _run_git(repo, ["fetch", "--prune", "--tags"], timeout_s=60.0)
        report["fetch"] = {
            "ok": fetch_result.ok,
            "stderr": fetch_result.stderr[-500:],
        }
        if not fetch_result.ok:
            report["reasons"].append("fetch_failed")

    branch = _run_git(repo, ["branch", "--show-current"])
    head = _run_git(repo, ["rev-parse", "HEAD"])
    upstream = _run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    remote_name = _run_git(repo, ["config", "--get", "branch.%s.remote" % branch.stdout]) if branch.stdout else GitResult(False)
    remote_url = _run_git(repo, ["remote", "get-url", remote_name.stdout]) if remote_name.ok and remote_name.stdout else GitResult(False)
    status = _run_git(repo, ["status", "--porcelain=v1", "-uall"])

    dirty_lines = status.stdout.splitlines() if status.stdout else []
    report.update({
        "branch": branch.stdout,
        "head": head.stdout,
        "upstream": upstream.stdout if upstream.ok else "",
        "remote_name": remote_name.stdout if remote_name.ok else "",
        "remote_url": remote_url.stdout if remote_url.ok else "",
        "dirty": bool(dirty_lines),
        "dirty_count": len(dirty_lines),
        "dirty_paths": [_first_line(line[3:]) for line in dirty_lines[:50]],
    })

    if not head.ok:
        report["state"] = "invalid_head"
        report["reasons"].append("invalid_head")
        return report
    if dirty_lines:
        report["reasons"].append("dirty_worktree")
        report["actions"].append("preserve_or_commit_local_changes_before_sync")
    if not upstream.ok or not upstream.stdout:
        report["reasons"].append("missing_upstream")
        report["actions"].append("set_or_confirm_tracking_branch")
        report["state"] = "blocked"
        report["ok"] = False
        return report

    counts = _run_git(repo, ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"])
    if counts.ok and counts.stdout:
        parts = counts.stdout.split()
        if len(parts) >= 2:
            report["ahead"] = int(parts[0])
            report["behind"] = int(parts[1])
    else:
        report["reasons"].append("ahead_behind_unavailable")

    ahead = int(report.get("ahead", 0) or 0)
    behind = int(report.get("behind", 0) or 0)
    if ahead and behind:
        report["reasons"].append("diverged_from_upstream")
        report["actions"].append("manual_rebase_or_merge_required")
    elif ahead:
        report["reasons"].append("local_ahead_of_upstream")
        report["actions"].append("push_or_open_pr")
    elif behind:
        report["reasons"].append("local_behind_upstream")
        if not dirty_lines:
            report["actions"].append("fast_forward_pull_available")

    if not report["reasons"]:
        report["state"] = "synced"
        report["ok"] = True
    elif report["reasons"] == ["local_behind_upstream"] and not dirty_lines:
        report["state"] = "behind_clean"
        report["ok"] = False
    else:
        report["state"] = "blocked"
        report["ok"] = False
    return report


def _event_path(base_dir: Optional[str]) -> Path:
    root = Path(base_dir).expanduser() if base_dir else Path(os.environ.get("VERSION_DRIFT_DIR", Path.cwd()))
    return root / _EVENT_STORE


def record_event(report: Dict[str, Any], base_dir: Optional[str] = None, event: str = "scan") -> Dict[str, Any]:
    payload = dict(report)
    payload["event"] = event
    path = _event_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return payload


def scan_projects(
    roots: Iterable[str],
    base_dir: Optional[str] = None,
    fetch: bool = False,
    max_depth: int = 5,
) -> Dict[str, Any]:
    projects = discover_projects(roots, max_depth=max_depth)
    reports = [inspect_project(str(path), fetch=fetch) for path in projects]
    for report in reports:
        record_event(report, base_dir=base_dir, event="scan")
    summary = summarize(reports)
    return {"summary": summary, "projects": reports}


def summarize(reports: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    states: Dict[str, int] = {}
    for report in reports:
        state = str(report.get("state", "unknown"))
        states[state] = states.get(state, 0) + 1
    return {
        "total": len(reports),
        "states": states,
        "ok": all(bool(r.get("ok")) for r in reports) if reports else True,
        "drifted": [r["path"] for r in reports if not r.get("ok")],
    }


def sync_project(path: str, base_dir: Optional[str] = None, apply: bool = False, fetch: bool = True) -> Dict[str, Any]:
    before = inspect_project(path, fetch=fetch)
    result: Dict[str, Any] = {"before": before, "applied": False, "after": None}
    if before.get("state") != "behind_clean":
        result["blocked"] = True
        result["reason"] = "only_clean_fast_forward_sync_is_allowed"
        record_event(before, base_dir=base_dir, event="sync_blocked")
        return result
    if not apply:
        result["blocked"] = False
        result["reason"] = "dry_run_fast_forward_available"
        record_event(before, base_dir=base_dir, event="sync_dry_run")
        return result
    repo = Path(str(before["path"]))
    pull = _run_git(repo, ["pull", "--ff-only"], timeout_s=120.0)
    result["pull"] = {"ok": pull.ok, "stdout": pull.stdout[-1000:], "stderr": pull.stderr[-1000:]}
    after = inspect_project(str(repo), fetch=False)
    result["after"] = after
    result["applied"] = pull.ok and bool(after.get("ok"))
    record_event(after, base_dir=base_dir, event="sync_applied" if result["applied"] else "sync_failed")
    return result


def _print_human_scan(result: Dict[str, Any]) -> None:
    summary = result["summary"]
    print("MemKraft version-drift scan")
    print("  total: %s" % summary["total"])
    for state, count in sorted(summary["states"].items()):
        print("  %s: %s" % (state, count))
    for report in result["projects"]:
        marker = "OK" if report.get("ok") else "DRIFT"
        print("  [%s] %s" % (marker, report.get("path")))
        reasons = report.get("reasons") or []
        if reasons:
            print("       reasons: %s" % ", ".join(reasons))


def cmd(args: argparse.Namespace) -> int:
    base_dir = getattr(args, "base_dir", "") or None
    if args.project_sync_command == "scan":
        roots = args.root or default_roots() or [os.getcwd()]
        result = scan_projects(roots, base_dir=base_dir, fetch=args.fetch, max_depth=args.max_depth)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            _print_human_scan(result)
        return 0 if result["summary"].get("ok") else 1
    if args.project_sync_command == "inspect":
        report = inspect_project(args.path, fetch=args.fetch)
        record_event(report, base_dir=base_dir, event="inspect")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True) if args.json else report)
        return 0 if report.get("ok") else 1
    if args.project_sync_command == "sync":
        result = sync_project(args.path, base_dir=base_dir, apply=args.apply, fetch=not args.no_fetch)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True) if args.json else result)
        return 0 if result.get("applied") or (not args.apply and not result.get("blocked")) else 1
    raise SystemExit("unknown project-sync command")
