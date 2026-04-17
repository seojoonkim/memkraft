<img src="assets/memkraft-banner.webp" alt="MemKraft - Zero-dependency compound memory for AI agents" width="100%">

# MemKraft 🧠

**v0.8.1** · Ultimate zero-dependency compound knowledge system for AI agents. Auto-extract, classify, search, and maintain memory in plain Markdown. **Debugging is memory. Time travel is memory. Multi-agent handoffs are memory. Facts have bitemporal validity. Memories decay reversibly. Wiki links build graphs.**

> **Plain Markdown source-of-truth · zero deps · zero keys.**
> In 30 seconds: `pipx install memkraft && memkraft init && memkraft agents-hint claude-code`

<div align="center">

<br>

[![PyPI][pypi-badge]][pypi-url]
[![Python][python-badge]][pypi-url]
[![License][license-badge]][license-url]
[![Tests][tests-badge]](#)
[![Dependencies][deps-badge]][pypi-url]

[pypi-badge]: https://img.shields.io/pypi/v/memkraft?style=for-the-badge&color=blue
[python-badge]: https://img.shields.io/badge/python-3.9%2B-blue?style=for-the-badge
[license-badge]: https://img.shields.io/badge/license-MIT-green?style=for-the-badge
[tests-badge]: https://img.shields.io/badge/tests-515%20passed-brightgreen?style=for-the-badge
[deps-badge]: https://img.shields.io/badge/dependencies-zero-brightgreen?style=for-the-badge
[pypi-url]: https://pypi.org/project/memkraft/
[license-url]: LICENSE

<br>

[Quick Start](#quick-start) · [Features](#features) · [API Reference](#api-reference) · [CLI Reference](#cli-reference) · [Architecture](#architecture) · [Changelog](#changelog)

</div>

<br>

## Quick Start

### 30-second quickstart

```bash
pipx install memkraft
memkraft init                                 # creates ./memory/ structure
memkraft agents-hint claude-code              # print a ready-to-paste AGENTS.md block
memkraft doctor                               # sanity-check your setup
```

That's it. No API keys, no database, no config. Plain Markdown files you own.

### Optional extras

```bash
pip install 'memkraft[mcp]'      # run `python -m memkraft.mcp` as an MCP server
pip install 'memkraft[watch]'    # run `memkraft watch` for auto-reindex on file save
pip install 'memkraft[all]'      # both of the above
```

### Install

```bash
pipx install memkraft
```

### CLI Usage

```bash
memkraft init
memkraft extract "Simon Kim is the CEO of Hashed in Seoul." --source "news"
memkraft brief "Simon Kim"
```

### Agent integration in one command (⭐ new in 0.8.1)

`memkraft agents-hint` prints a copy-paste integration block for whichever
agent framework you're using:

```bash
memkraft agents-hint claude-code       # AGENTS.md block for Claude Code
memkraft agents-hint openclaw          # AGENTS.md block for ОpenClaw
memkraft agents-hint openai            # OpenAI function-calling schemas + dispatcher
memkraft agents-hint cursor            # .cursorrules block
memkraft agents-hint mcp               # claude_desktop_config.json snippet
memkraft agents-hint langchain         # LangChain StructuredTool wrappers
```

Pipe the output straight into your config:

```bash
memkraft agents-hint claude-code >> AGENTS.md
```

See [`examples/`](examples/) for runnable variants.

### Python Usage

```python
from memkraft import MemKraft

mk = MemKraft("/path/to/memory")
mk.init()  # returns {"created": [...], "exists": [...], "base_dir": "..."}

# Extract entities & facts from text
mk.extract_conversations("Simon Kim is the CEO of Hashed.", source="news")

# Track an entity
mk.track("Simon Kim", entity_type="person", source="news")
mk.update("Simon Kim", info="Launched MemKraft", source="X/@simonkim_nft")

# Search with fuzzy matching
results = mk.search("venture capital", fuzzy=True)

# Agentic multi-hop search with context-aware re-ranking
results = mk.agentic_search(
    "who is the CEO of Hashed",
    context="crypto investment research",  # Conway SMS: same query, different context → different ranking
    file_back=True,  # feedback loop: results auto-filed back to entity timelines
)

# Run health check (5 self-diagnostic assertions)
report = mk.health_check()
# → {"pass_rate": 80.0, "health_score": "A", ...}

# Dream Cycle - nightly maintenance
mk.dream(dry_run=True)
```

<details>
<summary><b>More CLI examples - 6 daily patterns that cover 90% of use</b></summary>

<br>

```bash
# 1. Extract & Track - auto-detect entities from any text
memkraft extract "Simon Kim is the CEO of Hashed in Seoul." --source "news"
memkraft extract "Revenue grew 85% YoY" --confidence verified --when "bull market"
memkraft track "Simon Kim" --type person --source "X/@simonkim_nft"
memkraft update "Simon Kim" --info "Launched MemKraft" --source "X/@simonkim_nft"

# 2. Search & Recall - find anything in your memory
memkraft search "venture capital" --fuzzy
memkraft search "Seoul VC" --file-back           # feedback loop: auto-file to timelines
memkraft lookup "Simon" --brain-first
memkraft agentic-search "who is the CEO of Hashed" --context "meeting prep"

# 3. Meeting Prep - compile all context before a meeting
memkraft brief "Simon Kim"
memkraft brief "Simon Kim" --file-back            # record brief generation in timeline
memkraft links "Simon Kim"

# 4. Ingest & Classify - inbox → structured pages (safe by default)
memkraft cognify            # recommend-only; add --apply to move files
memkraft detect "Jack Ma and 马化腾 discussed AI" --dry-run

# 5. Log & Reflect - structured audit trail
memkraft log --event "Deployed v0.3" --tags deploy --importance high
memkraft retro              # daily Well / Bad / Next retrospective

# 6. Maintain & Heal - Dream Cycle keeps memory healthy
memkraft health-check       # 5 assertions → pass rate + health score (A/B/C/D)
memkraft dream --dry-run    # nightly: sources, duplicates, bloated pages
memkraft resolve-conflicts --strategy confidence  # resolve contradictory facts
memkraft diff               # what changed since last maintenance?
memkraft open-loops         # find all unresolved items

# 7. Debug Hypothesis Tracking - "Debugging is Memory"
memkraft debug start "API returns 500 on POST /users"
memkraft debug hypothesis "Database connection timeout"
memkraft debug evidence "DB pool healthy" --result contradicts
memkraft debug reject --reason "DB is fine"
memkraft debug hypothesis "Request validation missing"
memkraft debug evidence "Empty POST triggers 500" --result supports
memkraft debug confirm
memkraft debug end "Added request body validation"
memkraft debug search-rejected "timeout"  # avoid past mistakes
```

</details>

<br>

## Features

### Ingestion & Extraction

| Feature | Description |
|---------|-------------|
| **Auto-extract** | Pipe any text in, get entities + facts out. Regex-based NER for EN, KR, CN, JP - no LLM calls. |
| **CJK detection** | 806 stopwords, 100 Chinese surnames, 85 Japanese surnames, Korean particle stripping. |
| **Cognify pipeline** | Routes `inbox/` items to the right directory. Recommend-only by default - `--apply` to move. |
| **Fact registry** | Extracts currencies, percentages, dates, quantities into a cross-domain index. |
| **Originals capture** | Save raw text verbatim - no paraphrasing. |
| **Confidence levels** | Tag facts as `verified` / `experimental` / `hypothesis`. Dream Cycle warns untagged facts. |
| **Applicability conditions** | `--when "condition" --when-not "condition"` - facts get `When:` / `When NOT:` metadata. |

### Search & Retrieval

| Feature | Description |
|---------|-------------|
| **Fuzzy search** | `difflib.SequenceMatcher`-based. Works offline, zero setup. |
| **Brain-first lookup** | Searches entities → notes → decisions → meetings. Stops after sufficient high-relevance results. |
| **Agentic search** | Multi-hop: decompose query → search → traverse `[[wiki-links]]` → re-rank by tier/recency/confidence/applicability. |
| **Goal-weighted re-ranking** | Conway SMS: same query with different `--context` produces different rankings. |
| **Feedback loop** | `--file-back`: search results auto-filed back to entity timelines (compound interest for memory). |
| **Progressive disclosure** | 3-level query: L1 index (~50 tokens) → L2 section headers → L3 full file. |
| **Backlinks** | `[[entity-name]]` cross-references. See every page that references an entity. |
| **Link suggestions** | Auto-suggest missing `[[wiki-links]]` based on known entity names. |

### Structure & Organization

| Feature | Description |
|---------|-------------|
| **Compiled Truth + Timeline** | Dual-layer entity model: mutable current state + append-only audit trail with `[Source:]` tags. |
| **Memory tiers** | Core / Recall / Archival - explicit context window priority. `promote` to reclassify. |
| **Memory type classification** | 8 types: identity, belief, preference, relationship, skill, episodic, routine, transient. |
| **Type-aware decay** | Identity memories decay 10x slower than routine memories. Differential decay multipliers. |
| **RESOLVER.md** | MECE classification tree - every piece of knowledge has exactly one destination. |
| **Source attribution** | Every fact tagged with `[Source: who, when, how]`. Enforced by Dream Cycle. |
| **Dialectic synthesis** | Auto-detect contradictory facts during `extract`, tag `[CONFLICT]`, generate `CONFLICTS.md`. |
| **Conflict resolution** | `resolve-conflicts --strategy newest|confidence|keep-both|prompt`. |
| **Live Notes** | Persistent tracking for people and companies. Auto-incrementing updates + timeline. |

### Maintenance & Audit

| Feature | Description |
|---------|-------------|
| **Dream Cycle** | Nightly auto-maintenance: missing sources, thin pages, duplicates, inbox age, bloated pages, daily notes. |
| **Debug Hypothesis Tracking** | OBSERVE → HYPOTHESIZE → EXPERIMENT → CONCLUDE flow. Track hypotheses, evidence, rejections. Auto-switch warning after 2 failures. Search past sessions to avoid repeating failed approaches. |
| **Health Check** | 5 self-diagnostic assertions: source attribution, orphan facts, duplicates, inbox freshness, unresolved conflicts. Pass rate % + health score (A/B/C/D). |
| **Memory decay** | Older, unaccessed memories naturally decay - type-aware differential curves. |
| **Fact dedup** | Detects and merges duplicate facts across entities. |
| **Auto-summarize** | Condenses bloated pages while preserving key information. |
| **Diff tracking** | See exactly what changed since the last Dream Cycle. |
| **Open loop tracking** | Finds all pending / TODO / FIXME items across memory. |

### Logging & Reflection

| Feature | Description |
|---------|-------------|
| **Session logging** | JSONL event trail with tags, importance, entity, task, and decision fields. |
| **Daily retrospective** | Auto-generated Well / Bad / Next from session events + file changes. |
| **Decision distillation** | Scans events and notes for decision candidates. EN + KR keyword matching. |
| **Meeting briefs** | One command compiles entity info, timeline, open threads, and a pre-meeting checklist. |

### Debugging

| Feature | Description |
|---------|-------------|
| ✅ **Debug Hypothesis Tracking** | OBSERVE→HYPOTHESIZE→EXPERIMENT→CONCLUDE loop with persistent failure memory. |

### 📸 Memory Snapshots & Time Travel (v0.5.1)

| Feature | Description |
|---------|-------------|
| **Snapshot** | Create a point-in-time manifest of all memory files (hash, size, summary, sections, fact count, link count). Optionally embed full content. |
| **Snapshot List** | List all saved snapshots, newest first, with labels and metadata. |
| **Snapshot Diff** | Compare two snapshots (or snapshot vs live state). Shows added, removed, modified, unchanged files with byte deltas. |
| **Time Travel** | Search memory *as it was* at a past snapshot. Answer "what did I know about X on March 1st?" |
| **Entity Timeline** | Track how a specific entity evolved across all snapshots — new, modified, unchanged, deleted states. |

### 🧠 Channel Context Memory + Task Continuity + Agent Working Memory (v0.5.4)

| Feature | Description |
|---------|-------------|
| **Channel Context Memory** | Per-channel context persistence. Save/load/update context keyed by channel ID (e.g. `telegram-46291309`). Stored in `.memkraft/channels/{channel_id}.json`. |
| **Task Continuity Register** | Task lifecycle tracking with full history. `task_start` → `task_update` → `task_complete` + `task_history` + `task_list`. Each update stores timestamp + status + note. Stored in `.memkraft/tasks/{task_id}.json`. |
| **Agent Working Memory** | Per-agent persistent context. `agent_save` / `agent_load` any working memory dict. Stored in `.memkraft/agents/{agent_id}.json`. |
| **`agent_inject()`** | **The key feature.** Merges agent working memory + channel context + task history into a single ready-to-inject prompt block. Use this to give sub-agents full situational awareness. |

```python
from memkraft import MemKraft

mk = MemKraft("/path/to/memory")

# Save channel context
mk.channel_save("telegram-46291309", {
    "summary": "DM with Simon",
    "recent_tasks": ["vibekai deploy", "memkraft v0.5.4"],
    "preferences": {"language": "ko"},
})

# Register a task
mk.task_start("deploy-001", "Deploy vibekai to production",
              channel_id="telegram-46291309", agent="zeon")
mk.task_update("deploy-001", "active", "vercel build passed")

# Save agent working memory
mk.agent_save("zeon", {
    "key_context": "Simon's AI assistant",
    "active_tasks": ["deploy-001"],
    "learned": ["always report completion", "no silence"],
})

# Inject merged context block into a sub-agent instruction
block = mk.agent_inject("zeon",
                        channel_id="telegram-46291309",
                        task_id="deploy-001")
print(block)
# ## Agent Working Memory
# - **key_context:** Simon's AI assistant
# - **active_tasks:** deploy-001
# ...
# ## Channel Context
# - **summary:** DM with Simon
# ...
# ## Task Context
# - **Task:** Deploy vibekai to production
# - **Status:** active
# - **History:**
#   - [2026-04-15T...] active: vercel build passed
```

```python
from memkraft import MemKraft

mk = MemKraft("/path/to/memory")

# Take a snapshot before a big operation
snap = mk.snapshot(label="before-migration", include_content=True)

# ... time passes, memory changes ...

# What changed?
diff = mk.snapshot_diff(snap["snapshot_id"])  # vs live state
# → {added: [...], removed: [...], modified: [...], unchanged_count: 42}

# Search memory as it was at that snapshot
results = mk.time_travel("venture capital", snapshot_id=snap["snapshot_id"])

# How did an entity evolve over time?
timeline = mk.snapshot_entity("Simon Kim")
# → [{snapshot_id, timestamp, fact_count, size, change_type: "new"}, ...]
```

<br>

## 🐛 Debugging is Memory

Debugging insights are too valuable to lose in scrollback. MemKraft treats the entire debug process as first-class memory.

**The debug-hypothesis loop** - inspired by [Shen Huang's scientific debugging method](https://github.com/LichAmnesia/lich-skills/tree/main/skills/debug-hypothesis):

```
OBSERVE → HYPOTHESIZE → EXPERIMENT → CONCLUDE
    ↑                        |
    |    rejected?           |
    +←── next hypothesis ←───+
    |
    all rejected? → back to OBSERVE
```

- `mk.start_debug("bug description")` - begin a tracked session
- `mk.log_hypothesis(bug_id, "theory", "evidence")` - record each theory
- `mk.log_evidence(bug_id, hyp_id, "test result", "supports|contradicts")` - track proof
- `mk.reject_hypothesis(bug_id, hyp_id, "reason")` - mark failed approaches
- `mk.confirm_hypothesis(bug_id, hyp_id)` - lock in the root cause
- `mk.end_debug(bug_id, "resolution")` - close session, feed back to memory

**Why it matters:** rejected hypotheses are permanent memory. Next time you hit a similar bug, MemKraft surfaces what you already tried - no more repeating the same failed approaches.

<br>

## API Reference

### 🧠 Debugging is Memory

> **"Debugging is Memory"** — The reasoning chain matters as much as the fix. Every hypothesis, every piece of evidence, every rejection is permanently recorded. Future you (or your agent) can search past sessions to avoid repeating failed approaches.

```
OBSERVE → HYPOTHESIZE → EXPERIMENT → CONCLUDE
   │           │             │           │
   │     log_hypothesis  log_evidence  end_debug
   │           │             │           │
   │     reject/confirm   supports/     │
   │           │          contradicts    │
   │           │             │           │
   │     ⚠️ 2 failures      │      feedback loop
   │     → switch approach   │      → memory
   │                         │
   └─── search_rejected ─────┘
         "we already tried X"
```

**Key insights:**
- **2-fail auto-switch**: After 2 rejected hypotheses, warns "consider fundamentally different approach"
- **Anti-pattern detection**: `search_rejected_hypotheses("regex")` finds past failed approaches
- **Preserved failure memory**: All rejected hypotheses permanently searchable
- **Feedback loop**: Confirmed hypotheses auto-feed into entity timelines

```python
# Start a debug session
session = mk.start_debug("API returns 500 on POST /users")
bug_id = session["bug_id"]  # "DEBUG-20260413-120000"

# Hypothesis 1: test and reject
mk.log_hypothesis(bug_id, "Database connection timeout")
mk.log_evidence(bug_id, "H1", "DB pool healthy", result="contradicts")
mk.reject_hypothesis(bug_id, "H1", reason="DB is fine")

# Hypothesis 2: test and confirm
mk.log_hypothesis(bug_id, "Request validation missing")
mk.log_evidence(bug_id, "H2", "Empty POST triggers 500", result="supports")
mk.confirm_hypothesis(bug_id, "H2")

# End session — auto-feeds back into memory
mk.end_debug(bug_id, "Added request body validation middleware")

# Search past sessions to avoid repeating mistakes
mk.search_rejected_hypotheses("timeout")  # → "we already tried this and it failed"
mk.search_debug_sessions("POST /users")   # → find related past debugging
mk.debug_history(limit=10)                 # → recent sessions overview
```

<br>

## API Reference

### `MemKraft(base_dir=None)`

Initialize the memory system. If `base_dir` is not provided, uses `$MEMKRAFT_DIR` or `./memory`.

```python
from memkraft import MemKraft
mk = MemKraft("/path/to/memory")
```

### Core Methods

| Method | Description |
|--------|-------------|
| `init(path="")` | Create memory directory structure with all subdirectories and templates. |
| `track(name, entity_type="person", source="")` | Start tracking an entity. Creates a live-note in `live-notes/`. |
| `update(name, info, source="manual")` | Append new information to a tracked entity's timeline. |
| `brief(name, save=False, file_back=False)` | Generate a meeting brief for an entity. `file_back=True` records the brief generation in the entity timeline. |
| `promote(name, tier="core")` | Change memory tier: `core` / `recall` / `archival`. |
| `list_entities()` | List all tracked entities with their types. |

### Extraction & Classification

| Method | Description |
|--------|-------------|
| `extract_conversations(input_text, source="", dry_run=False, confidence="experimental", applicability="")` | Extract entities and facts from text. `confidence`: `verified` / `experimental` / `hypothesis`. `applicability`: `"When: X \| When NOT: Y"`. |
| `detect(text, source="", dry_run=False)` | Detect entities in text (EN/KR/CN/JP). |
| `cognify(dry_run=False, apply=False)` | Route inbox items to structured directories. Recommend-only by default. |
| `extract_facts_registry(text="")` | Extract numeric/date facts into cross-domain index. |
| `detect_conflicts(entity_name, new_fact, source="")` | Check for contradictory facts and tag with `[CONFLICT]`. |
| `resolve_conflicts(strategy="newest", dry_run=False)` | Resolve conflicts. Strategies: `newest`, `confidence`, `keep-both`, `prompt`. |
| `classify_memory_type(text)` | Classify text into one of 8 memory types. |

### Search

| Method | Description |
|--------|-------------|
| `search(query, fuzzy=False)` | Search memory files. Returns list of `{file, score, context, line}`. |
| `agentic_search(query, max_hops=2, json_output=False, context="", file_back=False)` | Multi-hop search with query decomposition, link traversal, and goal-weighted re-ranking. `context` enables Conway SMS reconstructive ranking. `file_back` enables the feedback loop. |
| `lookup(query, json_output=False, brain_first=False, full=False)` | Brain-first lookup: stop early on high-relevance hits unless `full=True`. |
| `query(query="", level=1, recent=0, tag="", date="")` | Progressive disclosure: L1=index, L2=sections, L3=full. |
| `links(name)` | Show backlinks to an entity (`[[wiki-links]]`). |

### Maintenance

| Method | Description |
|--------|-------------|
| `dream(date=None, dry_run=False, resolve_conflicts=False)` | Run Dream Cycle. 6 health checks + optional conflict resolution. |
| `health_check()` | Run 5 self-diagnostic assertions. Returns `{pass_rate, health_score, assertions}`. |
| `decay(days=90, dry_run=False)` | Flag stale facts. Type-aware: identity decays 10x slower than routine. |
| `dedup(dry_run=False)` | Find and merge duplicate facts. |
| `summarize(name=None, max_length=500)` | Auto-summarize bloated entity pages. |
| `diff()` | Show changes since last Dream Cycle. |
| `open_loops(dry_run=False)` | Find unresolved items (TODO/FIXME/pending). |
| `build_index()` | Build memory index at `.memkraft/index.json`. |
| `suggest_links()` | Suggest missing `[[wiki-links]]`. |

### Logging

| Method | Description |
|--------|-------------|
| `log_event(event, tags="", importance="normal", entity="", task="", decision="")` | Log a session event to JSONL. |
| `log_read(date=None)` | Read session events for a date. |
| `retro(dry_run=False)` | Generate daily retrospective (Well / Bad / Next). |
| `distill_decisions()` | Scan for decision candidates in events and notes. |

### Debug Hypothesis Tracking

| Method | Description |
|--------|-------------|
| `start_debug(bug_description)` | Start a new debug session. Returns `{bug_id, file, status}`. |
| `log_hypothesis(bug_id, hypothesis, evidence="", status="testing")` | Log a hypothesis. Auto-increments ID (H1, H2, ...). |
| `get_hypotheses(bug_id)` | Get all hypotheses for a debug session. |
| `reject_hypothesis(bug_id, hypothesis_id, reason="")` | Reject a hypothesis. Preserved permanently for future reference. |
| `confirm_hypothesis(bug_id, hypothesis_id)` | Confirm a hypothesis. Feeds back into memory. |
| `log_evidence(bug_id, hypothesis_id, evidence_text, result="neutral")` | Log evidence. Result: `supports` / `contradicts` / `neutral`. |
| `get_evidence(bug_id, hypothesis_id="")` | Get evidence entries, optionally filtered by hypothesis. |
| `end_debug(bug_id, resolution)` | End session with resolution. Auto-feeds to memory. |
| `get_debug_status(bug_id)` | Get current session status and hypothesis counts. |
| `debug_history(limit=10)` | List past debug sessions. |
| `search_debug_sessions(query)` | Search past sessions by description/hypothesis/resolution. |
| `search_rejected_hypotheses(query)` | Search rejected hypotheses — anti-pattern detector. |

### Memory Snapshots & Time Travel

| Method | Description |
|--------|-------------|
| `snapshot(label="", include_content=False)` | Create a point-in-time snapshot of all memory files. Returns `{snapshot_id, timestamp, label, file_count, total_bytes, path}`. |
| `snapshot_list()` | List all saved snapshots, newest first. |
| `snapshot_diff(snapshot_a, snapshot_b="")` | Compare two snapshots, or a snapshot vs live state. Returns `{added, removed, modified, unchanged_count}`. |
| `time_travel(query, snapshot_id="", date="")` | Search memory as it was at a past snapshot. Supports search by snapshot ID or date. |
| `snapshot_entity(name)` | Track how a specific entity evolved across all snapshots (new/modified/unchanged/deleted). |

<br>

## CLI Reference

```
memkraft <command> [options]
```

### Commands

| Command | Description |
|---------|-------------|
| `init [--path DIR]` | Initialize memory structure |
| `extract TEXT [--source S] [--dry-run] [--confidence C] [--when W] [--when-not W]` | Auto-extract entities and facts |
| `detect TEXT [--source S] [--dry-run]` | Detect entities in text (EN/KR/CN/JP) |
| `track NAME [--type T] [--source S]` | Start tracking an entity |
| `update NAME --info INFO [--source S]` | Update a tracked entity |
| `list` | List all tracked entities |
| `brief NAME [--save] [--file-back]` | Generate meeting brief |
| `promote NAME [--tier T]` | Change memory tier (core/recall/archival) |
| `search QUERY [--fuzzy] [--file-back]` | Search memory files |
| `agentic-search QUERY [--max-hops N] [--json] [--context C] [--file-back]` | Multi-hop agentic search |
| `lookup QUERY [--json] [--brain-first] [--full]` | Brain-first lookup |
| `query [QUERY] [--level 1\|2\|3] [--recent N] [--tag T] [--date D]` | Progressive disclosure query |
| `links NAME` | Show backlinks to an entity |
| `cognify [--dry-run] [--apply]` | Process inbox into structured pages |
| `log --event E [--tags T] [--importance I] [--entity E] [--task T] [--decision D]` | Log session event |
| `log --read [--date D]` | Read session events |
| `retro [--dry-run]` | Daily retrospective |
| `distill-decisions` | Scan for decision candidates |
| `health-check` | Run 5 self-diagnostic assertions → health score |
| `dream [--date D] [--dry-run] [--resolve-conflicts]` | Run Dream Cycle (nightly maintenance) |
| `resolve-conflicts [--strategy S] [--dry-run]` | Resolve fact conflicts |
| `decay [--days N] [--dry-run]` | Flag stale facts |
| `dedup [--dry-run]` | Find and merge duplicates |
| `summarize [NAME] [--max-length N]` | Auto-summarize bloated pages |
| `diff` | Show changes since last Dream Cycle |
| `open-loops [--dry-run]` | Find unresolved items |
| `index` | Build memory index |
| `suggest-links` | Suggest missing wiki-links |
| `extract-facts [TEXT]` | Extract numeric/date facts |
| `debug start DESC` | Start a new debug session (OBSERVE) |
| `debug hypothesis TEXT [--bug-id ID] [--evidence E]` | Log a hypothesis (HYPOTHESIZE) |
| `debug evidence TEXT [--bug-id ID] [--hypothesis-id H] [--result R]` | Log evidence (supports/contradicts/neutral) |
| `debug reject [--bug-id ID] [--hypothesis-id H] [--reason R]` | Reject current hypothesis |
| `debug confirm [--bug-id ID] [--hypothesis-id H]` | Confirm current hypothesis |
| `debug status [--bug-id ID]` | Show debug session status |
| `debug history [--limit N]` | List past debug sessions |
| `debug end RESOLUTION [--bug-id ID]` | End debug session (CONCLUDE) |
| `debug search QUERY` | Search past debug sessions |
| `debug search-rejected QUERY` | Search rejected hypotheses (anti-patterns) |
| `snapshot [--label L] [--include-content]` | Create a point-in-time memory snapshot |
| `snapshot-list` | List all saved snapshots (newest first) |
| `snapshot-diff SNAP_A [SNAP_B]` | Compare two snapshots or snapshot vs live state |
| `time-travel QUERY [--snapshot ID] [--date YYYY-MM-DD]` | Search memory as it was at a past snapshot |
| `snapshot-entity NAME` | Show how an entity evolved across snapshots |

<br>

## Architecture

```
Raw Input ──▶ Extract ──▶ Classify ──▶ Forge ──▶ Compound Knowledge
     ▲            │                                      │
     │        Confidence                                 │
     │        Applicability                              │
     │                                                   │
     └──── Feedback Loop ◄── Brain-first recall ◄───────┘
                              maintained by Dream Cycle + Health Check
```

### How It Works

**Zero dependencies.** Built entirely from Python stdlib: `re` for NER, `difflib` for fuzzy search, `json` for structured data, `pathlib` for file ops. No vector DB, no LLM calls at runtime, no framework lock-in.

**Compiled Truth + Timeline.** Every entity has two layers: a mutable *Compiled Truth* (current state) and an append-only *Timeline* with `[Source:]` tags. You get both "what we know now" and "how we got here."

**Auto-Extract pipeline.** Multi-stage NER: English Title Case → Korean particle stripping → Chinese surname detection (100 surnames) → Japanese surname detection (85 surnames) → fact extraction (`X is/was/leads Y`) → stopword filtering (806 KR/CN/JP stopwords).

**Goal-weighted re-ranking (Conway SMS).** `agentic_search("X", context="meeting prep")` and `agentic_search("X", context="investment analysis")` return different rankings from the same data. Memory type, confidence, and applicability conditions all factor into scoring.

**Feedback loop.** `--file-back` files search results back into entity timelines. Each query makes future queries richer - compound interest for memory.

**Health Check.** 5 assertions: (1) source attribution, (2) no orphan facts, (3) no duplicates, (4) inbox freshness, (5) no unresolved conflicts. Returns a pass rate and letter grade (A/B/C/D).

### Memory Directory Structure

```
memory/
├── .memkraft/           # Internal state (index.json, timestamps)
├── sessions/            # Structured event logs (YYYY-MM-DD.jsonl)
├── RESOLVER.md          # Classification decision tree (MECE)
├── TEMPLATES.md         # Page templates with tier labels
├── CONFLICTS.md         # Auto-generated conflict report
├── open-loops.md        # Unresolved items hub
├── fact-registry.md     # Cross-domain numeric/date facts
├── YYYY-MM-DD.md        # Daily notes
├── entities/            # People, companies, concepts (Tier: recall)
├── live-notes/          # Persistent tracking targets (Tier: core)
├── decisions/           # Decision records with rationale
├── originals/           # Captured verbatim - no paraphrasing
├── inbox/               # Quick capture before classification
├── tasks/               # Work-in-progress context
├── meetings/            # Briefs and notes
└── debug/               # Debug sessions (DEBUG-YYYYMMDD-HHMMSS.md)
```

<br>

## Comparison

| | **MemKraft** | **Mem0** | **Letta** |
|---|:---:|:---:|:---:|
| **Storage** | Plain Markdown | Vector + Graph DB | DB-backed |
| **Dependencies** | Zero | Vector DB + API | DB + runtime |
| **Offline / git-friendly** | ✅ | ❌ | ❌ |
| Auto-extract (EN/KR/CN/JP) | ✅ | ✅ (LLM) | - |
| Agentic search | ✅ | - | - |
| Goal-weighted re-ranking | ✅ | - | - |
| Feedback loop | ✅ | - | - |
| Confidence levels | ✅ | - | - |
| Health check | ✅ | - | - |
| Conflict detection & resolution | ✅ | - | - |
| Source attribution | Required | - | - |
| Dream Cycle | ✅ | - | - |
| Memory tiers | ✅ | - | ✅ |
| Type-aware decay | ✅ | - | - |
| Debug hypothesis tracking | ✅ | - | - |
| Memory snapshots & time travel | ✅ | ❌ | ❌ |
| Entity evolution timeline | ✅ | ❌ | ❌ |
| Snapshot diff | ✅ | ❌ | ❌ |
| **Semantic search** | ❌ | ✅ | - |
| **Graph memory** | ❌ | ✅ | - |
| **Self-editing memory** | ❌ | - | ✅ |
| **Cost** | Free | Free tier + paid | Free |

**Choose MemKraft when:** you want portable, git-friendly, zero-dependency memory that works with any agent framework, offline, forever.

**Choose something else when:** you need semantic/vector search, graph traversal, or a full agent runtime with virtual context management.

<br>

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) - use it however you want.

---

## Changelog

### v0.8.1 (2026-04-17)

The "connect-any-agent-in-30-seconds" release. **Fully backward-compatible.**

- **`mk.init()` now returns a dict** (`{"created": [...], "exists": [...], "base_dir": "..."}`) so scripts and tests can branch on it without parsing stdout.
- **`memkraft agents-hint <target>` CLI** — paste-ready integration blocks for `claude-code`, `openclaw`, `openai`, `cursor`, `mcp`, `langchain`. Supports `--format json` and `--base-dir`.
- **`examples/` folder** — drop-in AGENTS.md, OpenAI function-calling, 10-line RAG loop.
- **`python -m memkraft.mcp`** — MCP stdio server exposing `remember / search / recall / link`. Extras: `pip install 'memkraft[mcp]'`.
- **`memkraft watch`** — filesystem auto-reindex. Extras: `pip install 'memkraft[watch]'`.
- **`memkraft doctor`** — health check with 🟢/🟡/🔴 icons and fix hints.
- 515 tests passing (was 492, +23 new).

### v0.8.0 (2026-04-17)

Four new subsystems — all zero-dep, all backward-compatible.

**1. Bitemporal Fact Layer** — track facts with separate `valid_time` and `record_time`.
```python
mk.fact_add("Simon", "role", "CEO of Hashed", valid_from="2020-03-01")
mk.fact_add("Simon", "role", "CTO", valid_from="2018-01-01", valid_to="2020-02-29")
mk.fact_at("Simon", "role", as_of="2019-06-01")   # -> {"value": "CTO", ...}
mk.fact_history("Simon")                           # full timeline, recorded-order
mk.fact_invalidate("Simon", "role", invalid_at="2026-04-17")
```
Stored as inline Markdown markers in `memory/facts/<slug>.md` — human-readable, git-diffable.

**2. Memory Tier Labels + Working Set** — Letta-style `core | recall | archival` via a single YAML frontmatter line.
```python
mk.tier_set(memory_id, tier="core")
mk.tier_promote(memory_id)     # archival -> recall -> core
mk.tier_demote(memory_id)
mk.tier_list(tier="core")
mk.working_set(limit=10)       # all core + recently-accessed recall
```

**3. Reversible Decay + Tombstone** — memories fade numerically instead of being deleted, and tombstoned files move to `.memkraft/tombstones/` (still restorable).
```python
mk.decay_apply(memory_id, decay_rate=0.5)     # weight 1.0 -> 0.5
mk.decay_list(below_threshold=0.1)            # show faded memories
mk.decay_run(criteria={"weight_gt": 0.5})     # batch decay (cron)
mk.decay_tombstone(memory_id)                 # move to tombstones, still on disk
mk.decay_restore(memory_id)                   # full undo — weight back to 1.0
```

**4. Cross-Entity Link Graph + Backlinks** — `[[Wiki Link]]` patterns become a bidirectional graph; the file system is the DB.
```python
mk.link_scan()                                # build/refresh index
mk.link_backlinks("Simon")                    # files that mention [[Simon]]
mk.link_forward("inbox/notes.md")             # entities referenced from a file
mk.link_graph("Simon", hops=2)                # N-hop neighbourhood
mk.link_orphans()                             # entities referenced but never defined
```
Index persisted at `.memkraft/links/backlinks.json` and `.memkraft/links/forward.json`.

**Tests:** 409 → 492 (83 new across `test_v080_*`).

### v0.7.0 (2026-04-15)

- **`channel_update` modes:** `mode="append"` (list append) and `mode="merge"` (dict shallow merge) added. Default `mode="set"` unchanged — fully backward compatible.
- **Task delegation tracking:** `mk.task_delegate(task_id, from_agent, to_agent, context_note)` — delegate a task between agents with delegation events in history. `task_start()` gains optional `delegated_by` param.
- **`agent_inject` filters:** `max_history` (default 5) limits task history entries. `include_completed_tasks=True` includes completed channel tasks in the inject block.
- **Agent handoff:** `mk.agent_handoff(from_agent, to_agent, task_id, context_note)` — transfers working memory context, records handoff in `to_agent` memory, and delegates the task. Returns an inject-ready context block.
- **Channel task listing:** `mk.channel_tasks(channel_id, status, limit)` — filter tasks by channel and status (`active`/`completed`/`all`), sorted by creation time descending.
- **Task cleanup:** `mk.task_cleanup(max_age_days, archive)` — archive or delete completed tasks older than threshold. Archive goes to `.memkraft/tasks/archive/`.
- **New CLI commands:** `channel-update --mode`, `task-delegate`, `channel-tasks`, `agent-handoff`, `task-cleanup`
- **Tests:** 357 → 409 (52 new in `test_v070_multiagent.py`)

### v0.5.4 (2026-04-15)

- **Channel Context Memory:** `mk.channel_save()` / `mk.channel_load()` / `mk.channel_update()` — per-channel context persistence keyed by channel ID. Stored in `.memkraft/channels/{channel_id}.json`. Enables agents to recall channel-specific summaries, recent tasks, and preferences across sessions.
- **Task Continuity Register:** `mk.task_start()` / `mk.task_update()` / `mk.task_complete()` / `mk.task_history()` / `mk.task_list()` — full task lifecycle with timestamped history. Stored in `.memkraft/tasks/{task_id}.json`.
- **Agent Working Memory:** `mk.agent_save()` / `mk.agent_load()` / `mk.agent_inject()` — per-agent persistent working memory. The `agent_inject()` method merges agent memory + channel context + task history into a single ready-to-inject prompt block for sub-agent delegation.
- **CLI commands:** `channel-save/load`, `task-start/update/list`, `agent-save/load/inject`
- **zero-dependency maintained** (stdlib only: json, pathlib, datetime)
- Tests: 328 → 377 (49 new in `test_v054_context.py`)

### v0.5.1 (2026-04-14)

- **Memory Snapshots & Time Travel:** `mk.snapshot()` / `mk.snapshot_list()` / `mk.snapshot_diff()` / `mk.time_travel()` / `mk.snapshot_entity()` — create point-in-time snapshots of all memory files (hash, size, summary, sections, fact count, link count), compare any two snapshots to see what changed, search memory as it was at a past date, and track how individual entities evolved over time
- **CLI snapshot commands:** `memkraft snapshot` / `snapshot-list` / `snapshot-diff` / `time-travel` / `snapshot-entity`
- Snapshot manifests saved as JSON under `.memkraft/snapshots/` — zero-dependency, git-friendly
- Optional `--include-content` flag embeds full file text in snapshots for richer time-travel queries
- Date-based time travel: `time-travel "query" --date 2026-03-01` finds the closest snapshot on or before that date
- Tests: 277 → 328 (51 new for Snapshots & Time Travel)

### v0.4.1 (2026-04-13)

- README: comprehensive "Debugging is Memory" section with flow diagram, full API/CLI reference for debug methods
- README: Appendix — Inspirations & Credits (8 projects with links)
- Tests: 277 (79 new for Debug Hypothesis Tracking)

### v0.4.0 (2026-04-13)

- **Debug Hypothesis Tracking (Debugging is Memory):** `mk.start_debug()` / `mk.log_hypothesis()` / `mk.log_evidence()` / `mk.reject_hypothesis()` / `mk.confirm_hypothesis()` / `mk.end_debug()` - full OBSERVE→HYPOTHESIZE→EXPERIMENT→CONCLUDE loop with persistent failure memory, 2-fail auto-switch warning, anti-pattern detection via `search_rejected_hypotheses()`, and feedback into entity timelines
- **CLI debug commands:** `memkraft debug start|hypothesis|evidence|reject|confirm|status|history|search-rejected`
- Tests: 198 → 277

### v0.3.0 (2026-04-13)

- **Query-to-Memory Feedback Loop:** `agentic-search --file-back` / `search --file-back` - search results auto-filed back to entity timelines (compound interest for memory)
- **Confidence Levels:** All facts support `verified` / `experimental` / `hypothesis` tags; `extract --confidence verified`; Dream Cycle warns about untagged facts; agentic-search re-ranking weights by confidence; conflict resolution via `--strategy confidence`
- **Memory Health Assertions:** `memkraft health-check` - 5 self-diagnostic assertions (source attribution, orphan facts, duplicates, inbox freshness, unresolved conflicts) with pass rate % and health score (A/B/C/D); auto-runs in Dream Cycle
- **Applicability Conditions:** `extract --when "condition" --when-not "condition"` - facts get `When:` / `When NOT:` metadata; agentic-search boosts results matching current context's applicability conditions
- **Python re-export:** `from memkraft import MemKraft` now works directly
- Tests: 158 → 198

### v0.2.0 (2026-04-12)

- **Goal-Weighted Reconstructive Memory (Conway SMS):** `agentic-search --context` - same query with different context produces different result rankings; memory-type-aware re-ranking with differential decay curves
- **Dialectic Synthesis:** Auto-detect contradictory facts during `extract`, tag with `[CONFLICT]`, generate `CONFLICTS.md` report, resolve via `dream --resolve-conflicts` or `resolve-conflicts` command
- **Memory Type Classification:** 8 memory types (identity, belief, preference, relationship, skill, episodic, routine, transient) with differential decay multipliers
- **Type-Aware Decay:** Identity memories decay 10x slower than routine memories
- Tests: 112 → 158

### v0.1.0 (2026-04-12)

- Initial release: extract, detect, decay, dedup, summarize, agentic search
- Entity tracking (track, update, brief, promote)
- Dream Cycle (7 health checks), cognify, retro
- Hybrid search (exact + IDF-weighted + fuzzy), agentic multi-hop search
- Zero dependencies - stdlib only

---

<div align="center">

**MemKraft** - *Agents don't learn. They search. Until now.*

[GitHub](https://github.com/seojoonkim/memkraft) · [PyPI](https://pypi.org/project/memkraft/) · [Issues](https://github.com/seojoonkim/memkraft/issues)

## Appendix: Inspirations & Credits

MemKraft stands on the shoulders of giants. These projects and ideas shaped our approach:

| Project | Inspiration | Link |
|---------|------------|------|
| **Karpathy auto-research** | Evidence-based autonomous research methodology | [Tweet](https://x.com/karpathy/status/1906697764923920553) |
| **Shen Huang debug-hypothesis** | Scientific debugging: hypothesis-driven, max 5-line experiments | [GitHub](https://github.com/LichAmnesia/lich-skills/tree/main/skills/debug-hypothesis) · [Tweet](https://x.com/ShenHuang_/status/2043469166418735204) |
| **Letta (MemGPT)** | Tiered memory architecture (core / archival / recall) | [GitHub](https://github.com/letta-ai/letta) |
| **mem0** | Agent memory extraction and retrieval patterns | [GitHub](https://github.com/mem0ai/mem0) |
| **Zep** | Temporal memory decay and entity extraction | [GitHub](https://github.com/getzep/zep) |
| **MemoryWeaver** | Dialectic synthesis and memory reconstruction | [GitHub](https://github.com/pchaganti/gx-memory-weaver) |
| **Shubham Saboo's 6-agent system** | OpenClaw-based multi-agent + SOUL.md / MEMORY.md pattern | [Article](https://x.com/Saboo_Shubham_/article) |
| **Karpathy llm-wiki** | Wiki-style structured knowledge for LLMs | [Tweet](https://x.com/karpathy/status/2042079355925164424) |

*"If I have seen further, it is by standing on the shoulders of giants."*

Thank you to all these creators for sharing their work openly. MemKraft exists because of you.

</div>
