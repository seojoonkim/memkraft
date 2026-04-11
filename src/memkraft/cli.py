#!/usr/bin/env python3
"""MemKraft CLI — Command-line interface for the compound knowledge system"""

import argparse
import sys
from .core import MemKraft
from . import __version__


def main():
    parser = argparse.ArgumentParser(
        prog="memkraft",
        description="MemKraft — The compound knowledge system for AI agents",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
    detect_parser.add_argument("--dry-run", action="store_true", help="Preview without creating files")

    # dream
    dream_parser = subparsers.add_parser("dream", help="Run Dream Cycle (memory maintenance)")
    dream_parser.add_argument("--date", default=None, help="Date (YYYY-MM-DD)")
    dream_parser.add_argument("--dry-run", action="store_true", help="Preview without changes")

    # lookup
    lookup_parser = subparsers.add_parser("lookup", help="Brain-first lookup")
    lookup_parser.add_argument("query", help="Search query")
    lookup_parser.add_argument("--json", action="store_true", help="JSON output")
    lookup_parser.add_argument("--brain-first", action="store_true", help="Stop after high-relevance results")
    lookup_parser.add_argument("--full", action="store_true", help="Disable brain-first early stopping")

    # extract
    extract_parser = subparsers.add_parser("extract", help="Auto-extract entities and facts from text")
    extract_parser.add_argument("text", help="Text to analyze")
    extract_parser.add_argument("--source", default="", help="Source attribution")
    extract_parser.add_argument("--dry-run", action="store_true", help="Preview without creating files")

    # cognify
    cognify_parser = subparsers.add_parser("cognify", help="Process inbox into structured pages")
    cognify_parser.add_argument("--dry-run", action="store_true", help="Preview without moving files")
    cognify_parser.add_argument("--apply", action="store_true", help="Auto-move files (default: recommend-only)")

    # promote
    promote_parser = subparsers.add_parser("promote", help="Change memory tier for an entity")
    promote_parser.add_argument("name", help="Entity name")
    promote_parser.add_argument("--tier", default="core", choices=["core", "recall", "archival"], help="Target tier")

    # diff
    subparsers.add_parser("diff", help="Show changes since last Dream Cycle")

    # search
    search_parser = subparsers.add_parser("search", help="Search memory (with fuzzy matching)")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--fuzzy", action="store_true", help="Enable fuzzy matching")

    # links
    links_parser = subparsers.add_parser("links", help="Show backlinks to an entity")
    links_parser.add_argument("name", help="Entity name")

    # query
    query_parser = subparsers.add_parser("query", help="Progressive disclosure query (3 levels)")
    query_parser.add_argument("query", nargs="?", default="", help="Search query (optional)")
    query_parser.add_argument("--level", type=int, default=1, choices=[1, 2, 3], help="Detail level (1=index, 2=sections, 3=full)")
    query_parser.add_argument("--recent", type=int, default=0, help="Show N most recent files")
    query_parser.add_argument("--tag", default="", help="Filter by tag")
    query_parser.add_argument("--date", default="", help="Filter by date (YYYY-MM-DD)")

    # log
    log_parser = subparsers.add_parser("log", help="Log or read session events")
    log_parser.add_argument("--event", default="", help="Event description to log")
    log_parser.add_argument("--tags", default="", help="Comma-separated tags")
    log_parser.add_argument("--importance", default="normal", choices=["high", "medium", "normal", "low"], help="Importance level")
    log_parser.add_argument("--entity", default="", help="Related entity")
    log_parser.add_argument("--task", default="", help="Related task")
    log_parser.add_argument("--decision", default="", help="Decision made")
    log_parser.add_argument("--read", action="store_true", help="Read events")
    log_parser.add_argument("--date", default="", help="Date for reading events (YYYY-MM-DD)")

    # retro
    retro_parser = subparsers.add_parser("retro", help="Daily retrospective")
    retro_parser.add_argument("--dry-run", action="store_true", help="Preview without saving")

    # distill-decisions
    dd_parser = subparsers.add_parser("distill-decisions", help="Scan for decision candidates")
    dd_parser.add_argument("--dry-run", action="store_true", help="Preview only")

    # open-loops
    ol_parser = subparsers.add_parser("open-loops", help="Track unresolved items")
    ol_parser.add_argument("--dry-run", action="store_true", help="Preview without writing hub file")

    # index
    subparsers.add_parser("index", help="Build memory index")

    # suggest-links
    subparsers.add_parser("suggest-links", help="Suggest missing wiki-links")

    # extract-facts
    ef_parser = subparsers.add_parser("extract-facts", help="Extract numeric/date facts")
    ef_parser.add_argument("text", nargs="?", default="", help="Text to scan (default: scan memory files)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    mc = MemKraft()

    if args.command == "init":
        mc.init(args.path)
    elif args.command == "track":
        if not args.name.strip():
            print("❌ Error: name cannot be empty")
            sys.exit(1)
        mc.track(args.name, args.type, args.source)
    elif args.command == "update":
        mc.update(args.name, args.info, args.source)
    elif args.command == "list":
        mc.list_entities()
    elif args.command == "brief":
        mc.brief(args.name, save=args.save)
    elif args.command == "detect":
        mc.detect(args.text, args.source, dry_run=args.dry_run)
    elif args.command == "dream":
        mc.dream(date=args.date, dry_run=args.dry_run)
    elif args.command == "lookup":
        mc.lookup(args.query, json_output=args.json, brain_first=args.brain_first, full=args.full)
    elif args.command == "extract":
        mc.extract(args.text, source=args.source, dry_run=args.dry_run)
    elif args.command == "cognify":
        mc.cognify(dry_run=args.dry_run, apply=args.apply)
    elif args.command == "promote":
        mc.promote(args.name, tier=args.tier)
    elif args.command == "diff":
        mc.diff()
    elif args.command == "search":
        mc.search(args.query, fuzzy=args.fuzzy)
    elif args.command == "links":
        mc.links(args.name)
    elif args.command == "query":
        mc.query(args.query, level=args.level, recent=args.recent, tag=args.tag, date=args.date)
    elif args.command == "log":
        if args.read:
            mc.log_read(date=args.date)
        elif args.event:
            mc.log_event(args.event, tags=args.tags, importance=args.importance,
                         entity=args.entity, task=args.task, decision=args.decision)
        else:
            print("Use --event to log or --read to view events.")
    elif args.command == "retro":
        mc.retro(dry_run=args.dry_run)
    elif args.command == "distill-decisions":
        mc.distill_decisions(dry_run=args.dry_run)
    elif args.command == "open-loops":
        mc.open_loops(dry_run=args.dry_run)
    elif args.command == "index":
        mc.build_index()
    elif args.command == "suggest-links":
        mc.suggest_links()
    elif args.command == "extract-facts":
        mc.extract_facts_registry(text=args.text or "")

    return 0


if __name__ == "__main__":
    sys.exit(main())
