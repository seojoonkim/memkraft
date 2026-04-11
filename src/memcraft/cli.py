#!/usr/bin/env python3
"""MemCraft CLI — Command-line interface for the compound knowledge system"""

import argparse
import sys
from .core import MemCraft


def main():
    parser = argparse.ArgumentParser(
        prog="memcraft",
        description="MemCraft — The compound knowledge system for AI agents",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize memory structure")
    init_parser.add_argument("--path", default=".", help="Target directory (default: current)")

    # track
    track_parser = subparsers.add_parser("track", help="Start tracking an entity")
    track_parser.add_argument("name", help="Entity name")
    track_parser.add_argument("--type", default="person", choices=["person", "company", "concept", "topic"])
    track_parser.add_argument("--source", default="", help="Source attribution")

    # update
    update_parser = subparsers.add_parser("update", help="Update a tracked entity")
    update_parser.add_argument("name", help="Entity name")
    update_parser.add_argument("--info", required=True, help="New information")
    update_parser.add_argument("--source", default="manual", help="Source attribution")

    # list
    subparsers.add_parser("list", help="List all tracked entities")

    # brief
    brief_parser = subparsers.add_parser("brief", help="Generate meeting brief")
    brief_parser.add_argument("name", help="Entity name")
    brief_parser.add_argument("--save", action="store_true", help="Save to file")

    # detect
    detect_parser = subparsers.add_parser("detect", help="Detect entities in text")
    detect_parser.add_argument("text", help="Text to analyze")
    detect_parser.add_argument("--source", default="", help="Source attribution")
    detect_parser.add_argument("--no-llm", action="store_true", help="Regex-only mode")
    detect_parser.add_argument("--dry-run", action="store_true", help="Preview without creating files")

    # dream
    dream_parser = subparsers.add_parser("dream", help="Run Dream Cycle (memory maintenance)")
    dream_parser.add_argument("--date", default=None, help="Date (YYYY-MM-DD)")
    dream_parser.add_argument("--dry-run", action="store_true", help="Preview without changes")

    # lookup
    lookup_parser = subparsers.add_parser("lookup", help="Brain-first lookup")
    lookup_parser.add_argument("query", help="Search query")
    lookup_parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    mc = MemCraft()

    if args.command == "init":
        mc.init(args.path)
    elif args.command == "track":
        mc.track(args.name, args.type, args.source)
    elif args.command == "update":
        mc.update(args.name, args.info, args.source)
    elif args.command == "list":
        mc.list_entities()
    elif args.command == "brief":
        mc.brief(args.name, save=args.save)
    elif args.command == "detect":
        mc.detect(args.text, args.source, no_llm=args.no_llm, dry_run=args.dry_run)
    elif args.command == "dream":
        mc.dream(date=args.date, dry_run=args.dry_run)
    elif args.command == "lookup":
        mc.lookup(args.query, json_output=args.json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
