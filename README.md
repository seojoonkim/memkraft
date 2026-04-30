<img src="assets/memkraft-banner.webp" alt="MemKraft - Zero-dependency compound memory for AI agents" width="100%">

# MemKraft 🧠

> **Bitemporal memory × empirical tuning: the first self-improvement ledger for AI agents.**
> Your agent's accountable past, in plain Markdown.

**🏆 LongMemEval 98.0% — #1 on open-source agent long-term memory benchmarks**
_(Surpasses MemPalace 96.6%, MEMENTO by Microsoft 90.8% · LLM-as-judge · oracle 50 · 3-run semantic majority)_

**v2.7.0** · Zero-dependency compound knowledge system for AI agents. Auto-extract, classify, search, tune, and time-travel — all in plain Markdown. **Debugging is memory. Time travel is memory. Multi-agent handoffs are memory. Facts have bitemporal validity. Memories decay reversibly. Wiki links build graphs. Tuning iterations leave an audit trail.**

> **Plain Markdown source-of-truth · zero deps · zero keys · zero LLM calls inside MemKraft.**
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
[tests-badge]: https://img.shields.io/badge/tests-1168%20passed-brightgreen?style=for-the-badge
[deps-badge]: https://img.shields.io/badge/dependencies-zero-brightgreen?style=for-the-badge
[pypi-url]: https://pypi.org/project/memkraft/
[license-url]: LICENSE

<br>

</div>

[![PyPI](https://img.shields.io/pypi/v/memkraft.svg)](https://pypi.org/project/memkraft/)
[![Python](https://img.shields.io/pypi/pyversions/memkraft.svg)](https://pypi.org/project/memkraft/)
[![Downloads](https://img.shields.io/pypi/dm/memkraft.svg)](https://pypi.org/project/memkraft/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📖 Table of Contents

- [⚡ Quickstart (30s)](#-quickstart-30s)
- [🎯 Why MemKraft?](#-why-memkraft)
- [🧩 Features](#-features)
- [🍳 Real-world Recipes](#-real-world-recipes)
- [🔬 Specialized Features](#-specialized-features)
- [📚 API Reference](#-api-reference)
- [⌨️ CLI Reference](#-cli-reference)
- [🏗️ Architecture](#-architecture)
- [⚖️ Comparison](#-comparison)
- [🏆 Reproducing LongMemEval](#-reproducing-longmemeval-results)
- [🆙 Staying Up To Date](#-staying-up-to-date)
- [📝 Changelog](#-changelog)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)
- [🙏 Appendix: Inspirations & Credits](#-appendix-inspirations--credits)

<br>

## ⚡ Quickstart (30s)

```bash
pip install memkraft
memkraft init                   # → creates ./memory/ with RESOLVER, TEMPLATES, entities/, ...
memkraft agents-hint claude-code >> AGENTS.md   # your agent is now memory-aware
```

### Or scaffold a full project

```bash
memkraft init --template claude-code   # CLAUDE.md + memory/ + examples
memkraft init --template cursor        # .cursorrules + memory/
memkraft init --template mcp           # claude_desktop_config snippet + memory/
memkraft init --template rag           # retrieval-focused structure
memkraft init --template minimal       # just memory/entities/
memkraft templates list                # see all presets
```

Templates are **idempotent** — re-running `init --template X` never overwrites your edits.

Or in Python:

```python
from memkraft import MemKraft
mk = MemKraft("./memory"); mk.init()
mk.track("Simon Kim", entity_type="person", source="news")
mk.update("Simon Kim", info="Launched MemKraft 0.8.1", source="PyPI")
mk.search("MemKraft")
```

That's it. Your agent now has persistent memory as plain markdown files.
No API keys. No database. No config. Just `.md` files you own.

### Optional extras

```bash
pip install 'memkraft[mcp]'      # + MCP server  (`python -m memkraft.mcp`)
pip install 'memkraft[watch]'    # + auto-reindex on save (`memkraft watch`)
pip install 'memkraft[all]'      # everything
```

### Connect Any Agent in 30 Seconds

`memkraft agents-hint <target>` prints copy-paste-ready integration snippets:

```bash
memkraft agents-hint claude-code   # → CLAUDE.md / AGENTS.md block
memkraft agents-hint openclaw      # → AGENTS.md block for ОpenClaw
memkraft agents-hint cursor        # → .cursorrules block
memkraft agents-hint openai        # → Custom GPT / function-calling schema
memkraft agents-hint mcp           # → claude_desktop_config.json snippet
memkraft agents-hint langchain     # → LangChain StructuredTool wrappers
```

Paste the output. Done. Or pipe it straight into your config:

```bash
memkraft agents-hint claude-code >> AGENTS.md
```

See [`examples/`](examples/) for runnable variants.

<br>

## 🎯 Why MemKraft?

### What Makes MemKraft Different

|                        | **MemKraft**   | Mem0        | Letta    |
|------------------------|----------------|-------------|----------|
| Dependencies           | **0**          | many        | many     |
| API key required       | **No**         | Yes         | Yes      |
| Source of truth        | Plain `.md`    | Cloud/DB    | DB       |
| Local-first            | ✅             | —           | —        |
| Git-friendly           | ✅             | —           | —        |

### API overview (14 public methods)

| API | Since | Role |
|-----|-------|------|
| `track` | 0.5 | Start tracking an entity |
| `update` | 0.5 | Append information to an entity |
| `search` | 0.5 | Hybrid search (exact + IDF + fuzzy + BM25) |
| `tier_set` | 0.8 | Set tier: `core` / `recall` / `archival` |
| `fact_add` | 0.8 | Record a bitemporal fact (`fact_type`: `episodic` / `semantic` / `procedural` since **2.6**) |
| `log_event` | 0.8 | Log a timestamped event |
| `decision_record` | 0.9 | Capture a decision with rationale |
| `evidence_first` | 0.9 | Retrieve evidence before acting |
| `prompt_register` | **1.0** | Register a prompt/skill as an entity |
| `prompt_eval` | **1.0** | Record one tuning iteration |
| `prompt_evidence` | **1.0** | Cite past tuning results |
| `convergence_check` | **1.0** | Auto-judge convergence |
| `auto_tier` | **2.6** | Recommend `core` / `recall` / `archival` from `(recency, frequency, importance)`; `dry_run=True` by default |
| `cache_stats` | **2.7** | Inspect search cache hit/miss/eviction counters and current generation |

Also new in **2.6**: silent contradiction detection on `fact_add`, plus 1-hop graph neighbor expansion for counting-style queries (`how many`, `list all`).

New in **2.7**: in-process **search result caching** for `search_v2()` and `search_smart()` — thread-safe LRU + TTL (default capacity 256, TTL 300s). Mutations (`update`, `track`, `fact_add`, `log_event`, `consolidate`, `decision_record`, `dream_cycle`) auto-invalidate via a generation counter, so callers never need to think about cache coherence. Opt-out per call with `cache=False`. Measured **6.14x speedup** on a hot-path workload (152 → 931 qps) and **1.65x** on a 50/50 mixed workload — raw numbers in `benchmarks/v2.7.0-bench-result.json`. Zero breaking changes.

Self-improvement loop: **register → tune → recall → decide**, every step auditable and time-travelable. See [MIGRATION.md](./MIGRATION.md) for upgrading from 0.9.x (zero breaking changes).

### The 1.0 Self-Improvement Loop

Register a prompt/skill, record iterations, cite past evidence, and let
MemKraft auto-judge when to stop tuning — all in plain Markdown, no LLM
calls inside MemKraft:

```python
from memkraft import MemKraft
mk = MemKraft("./memory")

# 1. register a prompt/skill as a first-class entity
mk.prompt_register(
    "my-skill",
    path="skills/my-skill/SKILL.md",
    owner="zeon",
    tags=["tuning"],
)

# 2. record each empirical iteration (host agent dispatches the run
#    — MemKraft only persists the report)
mk.prompt_eval(
    "my-skill",
    iteration=1,
    scenarios=[{
        "name": "parallel-dispatch",
        "description": "3 subagents at once",
        "requirements": [{"item": "all return", "critical": True}],
    }],
    results=[{
        "scenario": "parallel-dispatch",
        "success": True, "accuracy": 85,
        "tool_uses": 5, "duration_ms": 2000,
        "unclear_points": ["schema missing"],
        "discretion": [],
    }],
)

# 3. cite past iterations before the next run
mk.prompt_evidence("my-skill", "accuracy regression")

# 4. stop when the last N iterations stabilise
verdict = mk.convergence_check("my-skill", window=2)
# -> {"converged": False, "reason": "insufficient-iters",
#     "iterations_checked": [1],
#     "suggested_next": "patch-and-iterate", ...}
```

Each call leaves an auditable trail on disk: a decision record per
iteration, an incident when unclear points pile up, and wiki-links
between iterations. Upgrade is zero-breaking from 0.9.x — see
[MIGRATION.md](./MIGRATION.md).

<br>
## 🧩 Features

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

<br>

## 🍳 Real-world Recipes

```bash
memkraft init
memkraft extract "Simon Kim is the CEO of Hashed in Seoul." --source "news"
memkraft brief "Simon Kim"
memkraft doctor                          # 🟢/🟡/🔴 health check with fix hints
memkraft doctor --fix --yes              # auto-repair missing structure (create-only, never deletes)
memkraft stats --export json             # workspace stats for CI dashboards
memkraft mcp doctor                      # validate MCP server readiness
memkraft mcp test                        # remember→search→recall smoke test
```

MCP (Claude Desktop / Cursor) setup: see [`docs/mcp-setup.md`](docs/mcp-setup.md).

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
## 🔬 Specialized Features

This section gathers features that deserve a closer look: time-travel snapshots, multi-agent context plumbing, autonomous memory lifecycle, and scientific debugging.

### 📸 Memory Snapshots & Time Travel (v0.5.1)

| Feature | Description |
|---------|-------------|
| **Snapshot** | Create a point-in-time manifest of all memory files (hash, size, summary, sections, fact count, link count). Optionally embed full content. |
| **Snapshot List** | List all saved snapshots, newest first, with labels and metadata. |
| **Snapshot Diff** | Compare two snapshots (or snapshot vs live state). Shows added, removed, modified, unchanged files with byte deltas. |
| **Time Travel** | Search memory *as it was* at a past snapshot. Answer "what did I know about X on March 1st?" |
| **Entity Timeline** | Track how a specific entity evolved across all snapshots — new, modified, unchanged, deleted states. |

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

### 🤖 Autonomous Memory Management (v1.1.0)

> *"Memory should manage itself."*

Memory tends to grow without limit — agents add entries but rarely clean up.
MemKraft 1.1.0 solves this with a self-managing lifecycle.

#### The Problem
- **Add-only pattern**: agents append to MEMORY.md every session, never prune
- **Silent maintenance failures**: nightly cleanup crons fail without notice
- **No lifecycle**: every memory entry treated equally, forever

#### The Solution: flush → compact → digest

```python
from memkraft import MemKraft
mk = MemKraft(base_dir="memory/")

# 1. Import existing MEMORY.md → structured MemKraft data
mk.flush("MEMORY.md")

# 2. Auto-archive old/low-priority items
result = mk.compact(max_chars=15000)
# → {"moved": 47, "freed_chars": 89400, ...}

# 3. Re-render MEMORY.md — always ≤ 15KB
mk.digest("MEMORY.md")
# → {"chars": 11700, "truncated": False}

# 4. Check memory health
health = mk.health()
# → {"status": "healthy", "total_chars": 11700, "recommendations": [...]}
```

#### Real-world result
Our MEMORY.md grew to **153KB** (1,862 lines) over weeks of agent sessions.
After `flush → compact → digest`: **11.7KB** (170 lines). **92% reduction.**

#### Nightly self-cleanup recipe
```python
# Watch for real-time sync
mk.watch("memory/", on_change="flush", interval=300)

# Or set a nightly schedule (requires: pip install memkraft[schedule])
mk.schedule([
    lambda: mk.compact(max_chars=15000),
    lambda: mk.digest("MEMORY.md"),
], cron_expr="0 23 * * *")
```

### 🐛 Debugging is Memory

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
## 📚 API Reference

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

## ⌨️ CLI Reference

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
| `selfupdate [--dry-run]` | Self-upgrade MemKraft via pip when newer version on PyPI |
| `doctor [--check-updates]` | Health check; with `--check-updates` also reports PyPI version status |

<br>
## 🏗️ Architecture

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

## ⚖️ Comparison

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

## 🏆 Reproducing LongMemEval Results

MemKraft achieves **98.0%** on [LongMemEval](https://github.com/xiaowu0162/LongMemEval) (LLM-as-judge, oracle subset, 3-run semantic majority vote). Single-run performance: **96–98%** (non-deterministic at inference level — sampling, not memory).

_Score measured on v1.0.2; v2.6.0 is regression-free with **1168 tests passing** and is API-compatible with the benchmark harness._

**Comparison vs prior SOTA:**
- MemKraft (v1.0.2 measurement) — **98.0%** (LLM-judge, oracle 50, 3-run majority)
- MemPalace — 96.6%
- MEMENTO/MS — 90.8%

### Setup

```bash
git clone https://github.com/seojoonkim/memkraft
cd memkraft
pip install -e ".[bench]"
```

### Run

```bash
cd benchmarks/longmemeval

# Single run (96% typical)
MODEL="claude-sonnet-4-6" \
  ANTHROPIC_API_KEY="your-key" \
  TAG="myrun" \
  python3 run.py 50 oracle

# LLM-as-judge scoring
MODEL="claude-sonnet-4-6" \
  ANTHROPIC_API_KEY="your-key" \
  python3 llm_judge.py

# 3-run majority vote (98% typical)
MODEL="claude-sonnet-4-6" \
  ANTHROPIC_API_KEY="your-key" \
  python3 run_majority_vote.py
```

### Notes

- **Dataset:** LongMemEval oracle subset (50 questions)
- **Judge:** LLM-as-judge (claude-sonnet-4-6) — semantic matching, not string match
- **98%** = 3-run semantic majority vote result
- **Single run:** 96~100% depending on inference sampling
- **Reproducibility note:** Variance comes from LLM inference sampling, not from MemKraft itself. Memory storage and retrieval are deterministic.

<br>

## 🆙 Staying Up To Date

MemKraft ships an opt-in self-upgrade flow so agents (and humans) never silently drift behind PyPI:

```bash
memkraft doctor --check-updates   # 🟢 up to date / 🟡 update available / 🔴 PyPI unreachable
memkraft selfupdate               # pip install -U memkraft when newer
memkraft selfupdate --dry-run     # check only
```

Classic still works:

```bash
pip install -U memkraft
```

**For agents:** add `memkraft doctor --check-updates` to your weekly skill or heartbeat — if it reports 🟡, ask the human before running `memkraft selfupdate`. Never auto-upgrade without explicit consent.

**For maintainers:** pushing a `vX.Y.Z` git tag triggers `.github/workflows/release.yml`, which builds, verifies (`twine check`), publishes to PyPI, and cuts a GitHub Release. Requires a `PYPI_API_TOKEN` repo secret — add it at `Settings → Secrets and variables → Actions`.

<br>
## 📝 Changelog

Highlights from recent releases. Full history: [CHANGELOG.md](CHANGELOG.md).

### v2.6.0 (current)

- **`auto_tier`** — recommend `core` / `recall` / `archival` from `(recency, frequency, importance)`; `dry_run=True` by default.
- **`fact_type`** on `fact_add` — `episodic` / `semantic` / `procedural` taxonomy with silent contradiction detection.
- **1-hop graph neighbor expansion** for counting-style queries (`how many`, `list all`).
- **1168 tests** passing; zero breaking changes from 2.5.x.

### v2.5.0

- Hybrid search upgraded to **exact + IDF + fuzzy + BM25**.
- Smarter ranking on multi-token queries; better recall for short answers.

### v2.4.0

- Cross-entity link graph hardened; faster `link_scan`, more reliable backlinks index.
- Performance improvements on large memory directories.

### v2.3.x

- Watchdog-based `memkraft watch` stability fixes.
- Doctor health hints; richer `--check-updates` output.

### v2.0.0

- Major API consolidation around the **register → tune → recall → decide** loop.
- Bitemporal facts, tier labels, reversible decay, link graph become first-class.
- **Zero breaking changes** from 0.9.x — see [MIGRATION.md](./MIGRATION.md).

### v1.1.0 — Autonomous Memory Management

`flush → compact → digest` self-managing lifecycle. See **🤖 Autonomous Memory Management** above for details.

### v1.0.0 — Self-Improvement Loop

`prompt_register` / `prompt_eval` / `prompt_evidence` / `convergence_check` make tuning a first-class, auditable workflow.

<details>
<summary><b>Earlier releases (v0.x — one-line summaries)</b></summary>

<br>

- **v0.8.1** (2026-04-17) — `agents-hint` CLI, `examples/`, `python -m memkraft.mcp`, `memkraft watch`, `memkraft doctor`. 515 tests.
- **v0.8.0** (2026-04-17) — Bitemporal Fact Layer + Memory Tier Labels + Reversible Decay/Tombstone + Cross-Entity Link Graph. 492 tests.
- **v0.7.0** (2026-04-15) — multi-agent: `channel_update` modes, task delegation, `agent_handoff`, channel task listing, task cleanup. 409 tests.
- **v0.5.4** (2026-04-15) — Channel Context Memory + Task Continuity Register + Agent Working Memory + `agent_inject()`. 377 tests.
- **v0.5.1** (2026-04-14) — Memory Snapshots & Time Travel: `snapshot` / `snapshot_list` / `snapshot_diff` / `time_travel` / `snapshot_entity`. 328 tests.
- **v0.4.1** (2026-04-13) — README: Debugging is Memory section + Appendix (Inspirations & Credits).
- **v0.4.0** (2026-04-13) — Debug Hypothesis Tracking: full OBSERVE→HYPOTHESIZE→EXPERIMENT→CONCLUDE loop, 2-fail auto-switch warning, `search_rejected_hypotheses()`. 277 tests.
- **v0.3.0** (2026-04-13) — Query-to-Memory Feedback Loop (`--file-back`), Confidence Levels, Memory Health Assertions, Applicability Conditions. 198 tests.
- **v0.2.0** (2026-04-12) — Goal-Weighted Reconstructive Memory (Conway SMS), Dialectic Synthesis, Memory Type Classification (8 types), Type-Aware Decay. 158 tests.
- **v0.1.0** (2026-04-12) — Initial release: extract, detect, decay, dedup, summarize, agentic search, entity tracking, Dream Cycle, hybrid search. Zero dependencies.

Full details for every release: [CHANGELOG.md](CHANGELOG.md).

</details>

<br>

## 🤝 Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

<br>

## 📄 License

[MIT](LICENSE) - use it however you want.

---

<div align="center">

**MemKraft** - *Agents don't learn. They search. Until now.*

[GitHub](https://github.com/seojoonkim/memkraft) · [PyPI](https://pypi.org/project/memkraft/) · [Issues](https://github.com/seojoonkim/memkraft/issues)

</div>

<br>

## 🙏 Appendix: Inspirations & Credits

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
