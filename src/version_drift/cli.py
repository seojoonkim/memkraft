"""Command-line interface for VersionDrift."""
from __future__ import annotations

import argparse
import json
import os
from typing import Optional

from .core import default_roots, inspect_project, record_event, scan_projects, sync_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="version-drift",
        description="Detect Git checkout drift and safely fast-forward clean projects",
    )
    parser.add_argument("--base-dir", default="", help="Directory for .version-drift/events.jsonl")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Discover projects under bounded roots")
    scan.add_argument("--root", action="append", default=[], help="Root to scan; repeatable")
    scan.add_argument("--max-depth", type=int, default=5)
    scan.add_argument("--fetch", action="store_true")
    scan.add_argument("--json", action="store_true")

    inspect = sub.add_parser("inspect", help="Inspect one Git project")
    inspect.add_argument("path")
    inspect.add_argument("--fetch", action="store_true")
    inspect.add_argument("--json", action="store_true")

    sync = sub.add_parser("sync", help="Safely fast-forward one clean project")
    sync.add_argument("path")
    sync.add_argument("--apply", action="store_true")
    sync.add_argument("--no-fetch", action="store_true")
    sync.add_argument("--json", action="store_true")
    return parser


def _print_scan(result: dict) -> None:
    summary = result["summary"]
    print("VersionDrift scan")
    print(f"  total: {summary['total']}")
    for state, count in sorted(summary["states"].items()):
        print(f"  {state}: {count}")
    for report in result["projects"]:
        marker = "OK" if report.get("ok") else "DRIFT"
        reasons = ", ".join(report.get("reasons") or [])
        print(f"  [{marker}] {report.get('path')}" + (f" ({reasons})" if reasons else ""))


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    base_dir = args.base_dir or None
    if args.command == "scan":
        roots = args.root or default_roots() or [os.getcwd()]
        result = scan_projects(roots, base_dir=base_dir, fetch=args.fetch, max_depth=args.max_depth)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            _print_scan(result)
        return 0 if result["summary"]["ok"] else 1
    if args.command == "inspect":
        report = inspect_project(args.path, fetch=args.fetch)
        record_event(report, base_dir=base_dir, event="inspect")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True) if args.json else report)
        return 0 if report.get("ok") else 1
    result = sync_project(args.path, base_dir=base_dir, apply=args.apply, fetch=not args.no_fetch)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True) if args.json else result)
    return 0 if result.get("applied") or (not args.apply and not result.get("blocked")) else 1


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["build_parser", "main"]


def cmd(args: argparse.Namespace) -> int:
    """Compatibility bridge for MemKraft's existing dispatcher."""
    argv = [args.project_sync_command]
    if getattr(args, "base_dir", ""):
        argv = ["--base-dir", args.base_dir, *argv]
    if args.project_sync_command == "scan":
        for root in getattr(args, "root", []) or []:
            argv.extend(["--root", root])
        argv.extend(["--max-depth", str(args.max_depth)])
        if getattr(args, "fetch", False): argv.append("--fetch")
        if getattr(args, "json", False): argv.append("--json")
    elif args.project_sync_command == "inspect":
        argv.append(args.path)
        if getattr(args, "fetch", False): argv.append("--fetch")
        if getattr(args, "json", False): argv.append("--json")
    else:
        argv.append(args.path)
        if getattr(args, "apply", False): argv.append("--apply")
        if getattr(args, "no_fetch", False): argv.append("--no-fetch")
        if getattr(args, "json", False): argv.append("--json")
    return main(argv)
