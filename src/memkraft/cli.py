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
    brief_parser.add_argument("--file-back", action="store_true", help="File brief generation back into entity timeline (feedback loop)")

    # detect
    detect_parser = subparsers.add_parser("detect", help="Detect entities in text")
    detect_parser.add_argument("text", help="Text to analyze")
    detect_parser.add_argument("--source", default="", help="Source attribution")
    detect_parser.add_argument("--dry-run", action="store_true", help="Preview without creating files")

    # dream
    dream_parser = subparsers.add_parser("dream", help="Run Dream Cycle (memory maintenance)")
    dream_parser.add_argument("--date", default=None, help="Date (YYYY-MM-DD)")
    dream_parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    dream_parser.add_argument("--resolve-conflicts", action="store_true", help="Auto-resolve detected fact conflicts (newest-wins strategy)")

    # lookup
    lookup_parser = subparsers.add_parser("lookup", help="Brain-first lookup")
    lookup_parser.add_argument("query", help="Search query")
    lookup_parser.add_argument("--json", action="store_true", help="JSON output")
    lookup_parser.add_argument("--brain-first", action="store_true", help="Stop after high-relevance results")
    lookup_parser.add_argument("--full", action="store_true", help="Disable brain-first early stopping")

    # extract
    extract_parser = subparsers.add_parser("extract", help="Auto-extract entities and facts from text")
    extract_parser.add_argument("input", nargs="?", default="", help="Markdown file or text to analyze (default: stdin)")
    extract_parser.add_argument("--source", default="", help="Source attribution")
    extract_parser.add_argument("--dry-run", action="store_true", help="Preview without creating files")
    extract_parser.add_argument("--confidence", default="experimental", choices=["verified", "experimental", "hypothesis"], help="Confidence level for extracted facts (default: experimental)")
    extract_parser.add_argument("--applicability", default="", help="Applicability condition (e.g., 'When: crypto bull market | When NOT: recession')")
    extract_parser.add_argument("--when", default="", help="Shorthand for applicability When: condition")
    extract_parser.add_argument("--when-not", default="", help="Shorthand for applicability When NOT: condition")

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
    search_parser.add_argument("--file-back", action="store_true", help="File results back into entity timelines (feedback loop)")

    # health-check
    subparsers.add_parser("health-check", help="Run memory health assertions (self-diagnostic)")

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

    # decay
    decay_parser = subparsers.add_parser("decay", help="Flag stale facts older than N days")
    decay_parser.add_argument("--days", type=int, default=90, help="Age threshold in days (default: 90)")
    decay_parser.add_argument("--dry-run", action="store_true", help="Preview without changes")

    # dedup
    dedup_parser = subparsers.add_parser("dedup", help="Find and merge duplicate facts")
    dedup_parser.add_argument("--dry-run", action="store_true", help="Preview without changes")

    # summarize
    summarize_parser = subparsers.add_parser("summarize", help="Auto-summarize entity pages")
    summarize_parser.add_argument("name", nargs="?", default=None, help="Entity name (default: all bloated pages)")
    summarize_parser.add_argument("--max-length", type=int, default=500, help="Max summary length in chars")

    # agentic-search
    agentic_parser = subparsers.add_parser("agentic-search", help="Multi-step agentic search (decompose + traverse + re-rank)")
    agentic_parser.add_argument("query", help="Search query")
    agentic_parser.add_argument("--max-hops", type=int, default=2, help="Max link traversal hops (default: 2)")
    agentic_parser.add_argument("--json", action="store_true", help="JSON output")
    agentic_parser.add_argument("--context", default="", help="Goal context for reconstructive re-ranking (Conway SMS)")
    agentic_parser.add_argument("--file-back", action="store_true", help="File results back into entity timelines (feedback loop)")

    # resolve-conflicts
    rc_parser = subparsers.add_parser("resolve-conflicts", help="Resolve detected fact conflicts")
    rc_parser.add_argument("--strategy", default="newest", choices=["newest", "confidence", "keep-both", "prompt"], help="Resolution strategy")
    rc_parser.add_argument("--dry-run", action="store_true", help="Preview without changes")

    # debug
    debug_parser = subparsers.add_parser("debug", help="Debug hypothesis tracking")
    debug_sub = debug_parser.add_subparsers(dest="debug_command", help="Debug subcommands")

    # debug start
    ds_parser = debug_sub.add_parser("start", help="Start a new debug session")
    ds_parser.add_argument("description", help="Bug description")

    # debug hypothesis
    dh_parser = debug_sub.add_parser("hypothesis", help="Log a hypothesis")
    dh_parser.add_argument("hypothesis", help="Hypothesis text")
    dh_parser.add_argument("--bug-id", default="", help="Bug ID (default: latest active session)")
    dh_parser.add_argument("--evidence", default="", help="Initial evidence")

    # debug evidence
    de_parser = debug_sub.add_parser("evidence", help="Log evidence")
    de_parser.add_argument("evidence", help="Evidence text")
    de_parser.add_argument("--bug-id", default="", help="Bug ID (default: latest active session)")
    de_parser.add_argument("--hypothesis-id", default="", help="Hypothesis ID (default: latest testing hypothesis)")
    de_parser.add_argument("--result", default="neutral", choices=["supports", "contradicts", "neutral"], help="Evidence result")

    # debug reject
    dr_parser = debug_sub.add_parser("reject", help="Reject current hypothesis")
    dr_parser.add_argument("--bug-id", default="", help="Bug ID")
    dr_parser.add_argument("--hypothesis-id", default="", help="Hypothesis ID")
    dr_parser.add_argument("--reason", default="", help="Rejection reason")

    # debug confirm
    dc_parser = debug_sub.add_parser("confirm", help="Confirm current hypothesis")
    dc_parser.add_argument("--bug-id", default="", help="Bug ID")
    dc_parser.add_argument("--hypothesis-id", default="", help="Hypothesis ID")

    # debug status
    dst_parser = debug_sub.add_parser("status", help="Show debug session status")
    dst_parser.add_argument("--bug-id", default="", help="Bug ID (default: latest)")

    # debug history
    dhist_parser = debug_sub.add_parser("history", help="List past debug sessions")
    dhist_parser.add_argument("--limit", type=int, default=10, help="Max sessions to show")

    # debug end
    dend_parser = debug_sub.add_parser("end", help="End debug session")
    dend_parser.add_argument("resolution", help="Resolution description")
    dend_parser.add_argument("--bug-id", default="", help="Bug ID")

    # debug search
    dsearch_parser = debug_sub.add_parser("search", help="Search past debug sessions")
    dsearch_parser.add_argument("query", help="Search query")

    # debug search-rejected
    dsr_parser = debug_sub.add_parser("search-rejected", help="Search rejected hypotheses")
    dsr_parser.add_argument("query", help="Search query")

    # snapshot
    snap_parser = subparsers.add_parser("snapshot", help="Create a point-in-time memory snapshot")
    snap_parser.add_argument("--label", default="", help="Human-readable label (e.g. 'before-migration')")
    snap_parser.add_argument("--include-content", action="store_true", help="Embed full file content (larger but enables richer time-travel)")

    # snapshot-list
    subparsers.add_parser("snapshot-list", help="List all saved snapshots")

    # snapshot-diff
    sdiff_parser = subparsers.add_parser("snapshot-diff", help="Compare two snapshots (or snapshot vs live)")
    sdiff_parser.add_argument("snapshot_a", help="First snapshot ID (the 'before')")
    sdiff_parser.add_argument("snapshot_b", nargs="?", default="", help="Second snapshot ID (default: compare to live state)")

    # time-travel
    tt_parser = subparsers.add_parser("time-travel", help="Search memory as it was at a past snapshot")
    tt_parser.add_argument("query", help="Search query")
    tt_parser.add_argument("--snapshot", default="", help="Specific snapshot ID")
    tt_parser.add_argument("--date", default="", help="Date (YYYY-MM-DD) — uses closest snapshot on or before this date")

    # snapshot-entity
    se_parser = subparsers.add_parser("snapshot-entity", help="Show how an entity evolved across snapshots")
    se_parser.add_argument("name", help="Entity name")

    # channel-save
    cs_parser = subparsers.add_parser("channel-save", help="Save channel context")
    cs_parser.add_argument("channel_id", help="Channel identifier (e.g. telegram-46291309)")
    cs_parser.add_argument("--summary", default="", help="Channel summary")
    cs_parser.add_argument("--data", default="", help="JSON string of context data")

    # channel-load
    cl_parser = subparsers.add_parser("channel-load", help="Load channel context")
    cl_parser.add_argument("channel_id", help="Channel identifier")

    # task-start
    ts_parser = subparsers.add_parser("task-start", help="Start a new task")
    ts_parser.add_argument("task_id", help="Task identifier")
    ts_parser.add_argument("--desc", required=True, help="Task description")
    ts_parser.add_argument("--channel", default="", help="Associated channel ID")
    ts_parser.add_argument("--agent", default="", help="Associated agent ID")

    # task-update
    tu_parser = subparsers.add_parser("task-update", help="Update a task")
    tu_parser.add_argument("task_id", help="Task identifier")
    tu_parser.add_argument("--status", required=True, help="New status")
    tu_parser.add_argument("--note", default="", help="Progress note")

    # task-list
    tl_parser = subparsers.add_parser("task-list", help="List tasks")
    tl_parser.add_argument("--status", default="active", help="Filter by status (default: active)")

    # agent-save
    as_parser = subparsers.add_parser("agent-save", help="Save agent working memory")
    as_parser.add_argument("agent_id", help="Agent identifier (e.g. zeon)")
    as_parser.add_argument("--context", default="", help="Key context string")
    as_parser.add_argument("--data", default="", help="JSON string of working memory")

    # agent-load
    al_parser = subparsers.add_parser("agent-load", help="Load agent working memory")
    al_parser.add_argument("agent_id", help="Agent identifier")

    # agent-inject
    ai_parser = subparsers.add_parser("agent-inject", help="Inject merged context block")
    ai_parser.add_argument("agent_id", help="Agent identifier")
    ai_parser.add_argument("--channel", default="", help="Channel ID to include")
    ai_parser.add_argument("--task", default="", help="Task ID to include")
    ai_parser.add_argument("--max-history", type=int, default=5, help="Max task history entries (default: 5)")
    ai_parser.add_argument("--include-completed-tasks", action="store_true", help="Include completed tasks for the channel")

    # channel-update (enhanced)
    cu_parser = subparsers.add_parser("channel-update", help="Update a channel context field")
    cu_parser.add_argument("channel_id", help="Channel identifier")
    cu_parser.add_argument("key", help="Field name")
    cu_parser.add_argument("value", help="Field value (JSON for complex types)")
    cu_parser.add_argument("--mode", default="set", choices=["set", "append", "merge"], help="Update mode (default: set)")

    # task-delegate
    td_parser = subparsers.add_parser("task-delegate", help="Delegate a task between agents")
    td_parser.add_argument("task_id", help="Task identifier")
    td_parser.add_argument("from_agent", help="Delegating agent")
    td_parser.add_argument("to_agent", help="Receiving agent")
    td_parser.add_argument("--note", default="", help="Context note")

    # channel-tasks
    ct_parser = subparsers.add_parser("channel-tasks", help="List tasks for a channel")
    ct_parser.add_argument("channel_id", help="Channel identifier")
    ct_parser.add_argument("--status", default="all", choices=["active", "completed", "all"], help="Filter by status")
    ct_parser.add_argument("--limit", type=int, default=5, help="Max results (default: 5)")

    # agent-handoff
    ah_parser = subparsers.add_parser("agent-handoff", help="Hand off context between agents")
    ah_parser.add_argument("from_agent", help="Agent handing off")
    ah_parser.add_argument("to_agent", help="Agent receiving")
    ah_parser.add_argument("--task", default="", help="Task ID to include")
    ah_parser.add_argument("--note", default="", help="Context note")

    # task-cleanup
    tc_parser = subparsers.add_parser("task-cleanup", help="Clean up old completed tasks")
    tc_parser.add_argument("--max-age", type=int, default=30, help="Age threshold in days (default: 30)")
    tc_parser.add_argument("--archive", action="store_true", default=True, help="Archive tasks (default)")
    tc_parser.add_argument("--delete", action="store_true", help="Delete instead of archiving")

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
        mc.brief(args.name, save=args.save, file_back=getattr(args, 'file_back', False))
    elif args.command == "detect":
        mc.detect(args.text, args.source, dry_run=args.dry_run)
    elif args.command == "dream":
        mc.dream(date=args.date, dry_run=args.dry_run, resolve_conflicts=getattr(args, 'resolve_conflicts', False))
    elif args.command == "lookup":
        mc.lookup(args.query, json_output=args.json, brain_first=args.brain_first, full=args.full)
    elif args.command == "extract":
        # Build applicability from --applicability, --when, --when-not
        applicability = getattr(args, 'applicability', '')
        when_cond = getattr(args, 'when', '')
        when_not_cond = getattr(args, 'when_not', '')
        if when_cond and not applicability:
            applicability = f"When: {when_cond}"
        if when_not_cond:
            applicability = f"{applicability} | When NOT: {when_not_cond}".strip(' |')
        mc.extract_conversations(args.input, source=args.source, dry_run=args.dry_run,
                                 confidence=getattr(args, 'confidence', 'experimental'),
                                 applicability=applicability)
    elif args.command == "cognify":
        mc.cognify(dry_run=args.dry_run, apply=args.apply)
    elif args.command == "promote":
        mc.promote(args.name, tier=args.tier)
    elif args.command == "diff":
        mc.diff()
    elif args.command == "search":
        results = mc.search(args.query, fuzzy=args.fuzzy)
        if getattr(args, 'file_back', False) and results:
            mc._file_back_results(args.query, results)
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
        mc.distill_decisions()
    elif args.command == "open-loops":
        mc.open_loops(dry_run=args.dry_run)
    elif args.command == "index":
        mc.build_index()
    elif args.command == "suggest-links":
        mc.suggest_links()
    elif args.command == "extract-facts":
        mc.extract_facts_registry(text=args.text or "")
    elif args.command == "decay":
        mc.decay(days=args.days, dry_run=args.dry_run)
    elif args.command == "dedup":
        mc.dedup(dry_run=args.dry_run)
    elif args.command == "summarize":
        mc.summarize(name=args.name, max_length=args.max_length)
    elif args.command == "agentic-search":
        mc.agentic_search(args.query, max_hops=args.max_hops, json_output=args.json,
                          context=getattr(args, 'context', ''),
                          file_back=getattr(args, 'file_back', False))
    elif args.command == "health-check":
        mc.health_check()
    elif args.command == "resolve-conflicts":
        mc.resolve_conflicts(strategy=args.strategy, dry_run=args.dry_run)
    elif args.command == "debug":
        if not args.debug_command:
            debug_parser.print_help()
            return 0
        if args.debug_command == "start":
            mc.start_debug(args.description)
        elif args.debug_command == "hypothesis":
            bug_id = args.bug_id or _latest_debug_session(mc)
            if bug_id:
                mc.log_hypothesis(bug_id, args.hypothesis, evidence=args.evidence)
        elif args.debug_command == "evidence":
            bug_id = args.bug_id or _latest_debug_session(mc)
            h_id = args.hypothesis_id or _latest_testing_hypothesis(mc, bug_id)
            if bug_id and h_id:
                mc.log_evidence(bug_id, h_id, args.evidence, result=args.result)
        elif args.debug_command == "reject":
            bug_id = args.bug_id or _latest_debug_session(mc)
            h_id = args.hypothesis_id or _latest_testing_hypothesis(mc, bug_id)
            if bug_id and h_id:
                mc.reject_hypothesis(bug_id, h_id, reason=args.reason)
        elif args.debug_command == "confirm":
            bug_id = args.bug_id or _latest_debug_session(mc)
            h_id = args.hypothesis_id or _latest_testing_hypothesis(mc, bug_id)
            if bug_id and h_id:
                mc.confirm_hypothesis(bug_id, h_id)
        elif args.debug_command == "status":
            bug_id = args.bug_id or _latest_debug_session(mc)
            if bug_id:
                mc.get_debug_status(bug_id)
        elif args.debug_command == "history":
            mc.debug_history(limit=args.limit)
        elif args.debug_command == "end":
            bug_id = args.bug_id or _latest_debug_session(mc)
            if bug_id:
                mc.end_debug(bug_id, args.resolution)
        elif args.debug_command == "search":
            mc.search_debug_sessions(args.query)
        elif args.debug_command == "search-rejected":
            mc.search_rejected_hypotheses(args.query)
    elif args.command == "snapshot":
        mc.snapshot(label=args.label, include_content=getattr(args, 'include_content', False))
    elif args.command == "snapshot-list":
        mc.snapshot_list()
    elif args.command == "snapshot-diff":
        mc.snapshot_diff(args.snapshot_a, snapshot_b=args.snapshot_b)
    elif args.command == "time-travel":
        mc.time_travel(args.query, snapshot_id=getattr(args, 'snapshot', ''),
                       date=getattr(args, 'date', ''))
    elif args.command == "snapshot-entity":
        mc.snapshot_entity(args.name)
    elif args.command == "channel-save":
        import json as _json
        data = {}
        if args.data:
            try:
                data = _json.loads(args.data)
            except _json.JSONDecodeError:
                print("\u274c Invalid JSON in --data")
                sys.exit(1)
        if args.summary:
            data["summary"] = args.summary
        if not data:
            print("\u274c Provide --summary or --data")
            sys.exit(1)
        path = mc.channel_save(args.channel_id, data)
        print(f"\u2705 Channel context saved: {path}")
    elif args.command == "channel-load":
        import json as _json
        data = mc.channel_load(args.channel_id)
        if data:
            print(_json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(f"No context found for channel '{args.channel_id}'.")
    elif args.command == "task-start":
        import json as _json
        record = mc.task_start(args.task_id, args.desc,
                               channel_id=args.channel or None,
                               agent=args.agent or None)
        print(f"\u2705 Task started: {args.task_id}")
        print(_json.dumps(record, indent=2, ensure_ascii=False))
    elif args.command == "task-update":
        import json as _json
        record = mc.task_update(args.task_id, args.status, progress_note=args.note)
        if record:
            print(f"\u2705 Task updated: {args.task_id} \u2192 {args.status}")
        else:
            print(f"\u274c Task '{args.task_id}' not found.")
    elif args.command == "task-list":
        import json as _json
        tasks = mc.task_list(status=args.status)
        if tasks:
            print(f"\ud83d\udccb Tasks ({args.status}): {len(tasks)}")
            for t in tasks:
                print(f"  [{t.get('status', '?')}] {t['task_id']}: {t.get('description', '')[:60]}")
        else:
            print(f"No tasks with status '{args.status}'.")
    elif args.command == "agent-save":
        import json as _json
        data = {}
        if args.data:
            try:
                data = _json.loads(args.data)
            except _json.JSONDecodeError:
                print("\u274c Invalid JSON in --data")
                sys.exit(1)
        if args.context:
            data["key_context"] = args.context
        if not data:
            print("\u274c Provide --context or --data")
            sys.exit(1)
        path = mc.agent_save(args.agent_id, data)
        print(f"\u2705 Agent memory saved: {path}")
    elif args.command == "agent-load":
        import json as _json
        data = mc.agent_load(args.agent_id)
        if data:
            print(_json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(f"No working memory found for agent '{args.agent_id}'.")
    elif args.command == "agent-inject":
        block = mc.agent_inject(args.agent_id,
                                channel_id=args.channel or None,
                                task_id=args.task or None,
                                max_history=getattr(args, 'max_history', 5),
                                include_completed_tasks=getattr(args, 'include_completed_tasks', False))
        if block:
            print(block)
        else:
            print(f"No context available to inject for agent '{args.agent_id}'.")
    elif args.command == "channel-update":
        import json as _json
        value = args.value
        # Try to parse value as JSON for complex types
        try:
            value = _json.loads(value)
        except (_json.JSONDecodeError, ValueError):
            pass  # Keep as string
        result = mc.channel_update(args.channel_id, args.key, value, mode=args.mode)
        print(f"\u2705 Channel '{args.channel_id}' updated: {args.key} ({args.mode})")
        print(_json.dumps(result, indent=2, ensure_ascii=False))
    elif args.command == "task-delegate":
        import json as _json
        result = mc.task_delegate(args.task_id, args.from_agent, args.to_agent,
                                  context_note=args.note)
        if result:
            print(f"\u2705 Task '{args.task_id}' delegated: {args.from_agent} \u2192 {args.to_agent}")
        else:
            print(f"\u274c Task '{args.task_id}' not found.")
    elif args.command == "channel-tasks":
        import json as _json
        tasks = mc.channel_tasks(args.channel_id, status=args.status, limit=args.limit)
        if tasks:
            print(f"\ud83d\udccb Tasks for channel '{args.channel_id}' ({args.status}): {len(tasks)}")
            for t in tasks:
                print(f"  [{t.get('status', '?')}] {t['task_id']}: {t.get('description', '')[:60]}")
        else:
            print(f"No tasks found for channel '{args.channel_id}' ({args.status}).")
    elif args.command == "agent-handoff":
        block = mc.agent_handoff(args.from_agent, args.to_agent,
                                 task_id=args.task or None,
                                 context_note=args.note)
        if block:
            print(block)
        else:
            print(f"No context to hand off from '{args.from_agent}' to '{args.to_agent}'.")
    elif args.command == "task-cleanup":
        archive = not args.delete
        result = mc.task_cleanup(max_age_days=args.max_age, archive=archive)
        print(f"\ud83e\uddf9 Task cleanup: {result['archived']} archived, {result['deleted']} deleted, {result['kept']} kept")

    return 0


def _latest_debug_session(mc: MemKraft) -> str:
    """Find the latest active (non-CONCLUDE) debug session."""
    if not mc.debug_dir.exists():
        print("\u274c No debug sessions found. Use 'memkraft debug start' first.")
        return ""
    active = []
    concluded = []
    for md in sorted(mc.debug_dir.glob("DEBUG-*.md"), reverse=True):
        content = md.read_text(encoding="utf-8", errors="replace")
        if "**Status:** CONCLUDE" not in content:
            active.append(md.stem)
        else:
            concluded.append(md.stem)
    if active:
        return active[0]
    if concluded:
        return concluded[0]
    print("\u274c No debug sessions found.")
    return ""


def _latest_testing_hypothesis(mc: MemKraft, bug_id: str) -> str:
    """Find the latest testing hypothesis in a debug session."""
    if not bug_id:
        return ""
    hypotheses = mc.get_hypotheses(bug_id)
    testing = [h for h in hypotheses if h["status"] == "testing"]
    if testing:
        return testing[-1]["hypothesis_id"]
    if hypotheses:
        return hypotheses[-1]["hypothesis_id"]
    print(f"\u274c No hypotheses found in {bug_id}. Use 'memkraft debug hypothesis' first.")
    return ""


if __name__ == "__main__":
    sys.exit(main())
