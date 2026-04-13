<img src="assets/memkraft-banner.webp" alt="MemKraft — Zero-dependency compound memory for AI agents" width="100%">

# MemKraft 🧠

**v0.4.0** · Ultimate zero-dependency compound knowledge system for AI agents. Auto-extract, classify, search, and maintain memory in plain Markdown.

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
[tests-badge]: https://img.shields.io/badge/tests-259%20passed-brightgreen?style=for-the-badge
[deps-badge]: https://img.shields.io/badge/dependencies-zero-brightgreen?style=for-the-badge
[pypi-url]: https://pypi.org/project/memkraft/
[license-url]: LICENSE

<br>

[Quick Start](#quick-start) · [Features](#features) · [API Reference](#api-reference) · [CLI Reference](#cli-reference) · [Architecture](#architecture) · [Changelog](#changelog)

</div>

<br>

## Quick Start

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

No API keys. No database. No config. Plain Markdown files you own.

### Python Usage

```python
from memkraft import MemKraft

mk = MemKraft("/path/to/memory")
mk.init()

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

# Dream Cycle — nightly maintenance
mk.dream(dry_run=True)
```

<details>
<summary><b>More CLI examples — 6 daily patterns that cover 90% of use</b></summary>

<br>

```bash
# 1. Extract & Track — auto-detect entities from any text
memkraft extract "Simon Kim is the CEO of Hashed in Seoul." --source "news"
memkraft extract "Revenue grew 85% YoY" --confidence verified --when "bull market"
memkraft track "Simon Kim" --type person --source "X/@simonkim_nft"
memkraft update "Simon Kim" --info "Launched MemKraft" --source "X/@simonkim_nft"

# 2. Search & Recall — find anything in your memory
memkraft search "venture capital" --fuzzy
memkraft search "Seoul VC" --file-back           # feedback loop: auto-file to timelines
memkraft lookup "Simon" --brain-first
memkraft agentic-search "who is the CEO of Hashed" --context "meeting prep"

# 3. Meeting Prep — compile all context before a meeting
memkraft brief "Simon Kim"
memkraft brief "Simon Kim" --file-back            # record brief generation in timeline
memkraft links "Simon Kim"

# 4. Ingest & Classify — inbox → structured pages (safe by default)
memkraft cognify            # recommend-only; add --apply to move files
memkraft detect "Jack Ma and 马化腾 discussed AI" --dry-run

# 5. Log & Reflect — structured audit trail
memkraft log --event "Deployed v0.3" --tags deploy --importance high
memkraft retro              # daily Well / Bad / Next retrospective

# 6. Maintain & Heal — Dream Cycle keeps memory healthy
memkraft health-check       # 5 assertions → pass rate + health score (A/B/C/D)
memkraft dream --dry-run    # nightly: sources, duplicates, bloated pages
memkraft resolve-conflicts --strategy confidence  # resolve contradictory facts
memkraft diff               # what changed since last maintenance?
memkraft open-loops         # find all unresolved items
```

</details>

<br>

## Features

### Ingestion & Extraction

| Feature | Description |
|---------|-------------|
| **Auto-extract** | Pipe any text in, get entities + facts out. Regex-based NER for EN, KR, CN, JP — no LLM calls. |
| **CJK detection** | 806 stopwords, 100 Chinese surnames, 85 Japanese surnames, Korean particle stripping. |
| **Cognify pipeline** | Routes `inbox/` items to the right directory. Recommend-only by default — `--apply` to move. |
| **Fact registry** | Extracts currencies, percentages, dates, quantities into a cross-domain index. |
| **Originals capture** | Save raw text verbatim — no paraphrasing. |
| **Confidence levels** | Tag facts as `verified` / `experimental` / `hypothesis`. Dream Cycle warns untagged facts. |
| **Applicability conditions** | `--when "condition" --when-not "condition"` — facts get `When:` / `When NOT:` metadata. |

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
| **Memory tiers** | Core / Recall / Archival — explicit context window priority. `promote` to reclassify. |
| **Memory type classification** | 8 types: identity, belief, preference, relationship, skill, episodic, routine, transient. |
| **Type-aware decay** | Identity memories decay 10x slower than routine memories. Differential decay multipliers. |
| **RESOLVER.md** | MECE classification tree — every piece of knowledge has exactly one destination. |
| **Source attribution** | Every fact tagged with `[Source: who, when, how]`. Enforced by Dream Cycle. |
| **Dialectic synthesis** | Auto-detect contradictory facts during `extract`, tag `[CONFLICT]`, generate `CONFLICTS.md`. |
| **Conflict resolution** | `resolve-conflicts --strategy newest|confidence|keep-both|prompt`. |
| **Live Notes** | Persistent tracking for people and companies. Auto-incrementing updates + timeline. |

### Maintenance & Audit

| Feature | Description |
|---------|-------------|
| **Dream Cycle** | Nightly auto-maintenance: missing sources, thin pages, duplicates, inbox age, bloated pages, daily notes. |
| **Health Check** | 5 self-diagnostic assertions: source attribution, orphan facts, duplicates, inbox freshness, unresolved conflicts. Pass rate % + health score (A/B/C/D). |
| **Memory decay** | Older, unaccessed memories naturally decay — type-aware differential curves. |
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

## 🐛 Debugging is Memory

Debugging insights are too valuable to lose in scrollback. MemKraft treats the entire debug process as first-class memory.

**The debug-hypothesis loop** — inspired by [Shen Huang's scientific debugging method](https://github.com/LichAmnesia/lich-skills/tree/main/skills/debug-hypothesis):

```
OBSERVE → HYPOTHESIZE → EXPERIMENT → CONCLUDE
    ↑                        |
    |    rejected?           |
    +←── next hypothesis ←───+
    |
    all rejected? → back to OBSERVE
```

- `mk.start_debug("bug description")` — begin a tracked session
- `mk.log_hypothesis(bug_id, "theory", "evidence")` — record each theory
- `mk.log_evidence(bug_id, hyp_id, "test result", "supports|contradicts")` — track proof
- `mk.reject_hypothesis(bug_id, hyp_id, "reason")` — mark failed approaches
- `mk.confirm_hypothesis(bug_id, hyp_id)` — lock in the root cause
- `mk.end_debug(bug_id, "resolution")` — close session, feed back to memory

**Why it matters:** rejected hypotheses are permanent memory. Next time you hit a similar bug, MemKraft surfaces what you already tried — no more repeating the same failed approaches.

<br>

## API Reference

### Debug Hypothesis Tracking
```python
mk.start_debug("bug description") → {"bug_id": "DEBUG-..."}
mk.log_hypothesis(bug_id, "hypothesis", evidence="", status="testing")
mk.get_hypotheses(bug_id) → [{"hypothesis_id": "H1", "status": "testing", ...}]
mk.reject_hypothesis(bug_id, "H1", "reason")
mk.confirm_hypothesis(bug_id, "H1")
mk.log_evidence(bug_id, "H1", "evidence", "supports")
mk.end_debug(bug_id, "resolution")
```

### 🧠 Debugging is Memory

**Debugging is not just problem-solving—it's memory creation.** Every bug you encounter becomes structured, searchable memory for your next debugging session.

The `debug-hypothesis` flow follows **OBSERVE → HYPOTHESIZE → EXPERIMENT → CONCLUDE**:

1. **OBSERVE**: Log the bug with `start_debug()` → Creates `debug/DEBUG-YYYYMMDD-HHMMSS.md`
2. **HYPOTHESIZE**: `log_hypothesis()` → Generates H1, H2, H3... with status tracking
3. **EXPERIMENT**: `log_evidence()` → ✅ supports | ❌ contradicts | ➖ neutral
4. **CONCLUDE**: `end_debug()` → Feeds back into memory for future `agentic_search`

**Key insights:**
- **2-fail auto-switch**: After 2 rejected hypotheses, warns "consider fundamentally different approach"
- **Anti-pattern detection**: `search_rejected_hypotheses("regex")` finds past failed approaches
- **Preserved failure memory**: All rejected hypotheses permanently searchable
- **Feedback loop**: Confirmed hypotheses auto-feed into entity timelines

```bash
# CLI workflow
memkraft debug start "API 500 on POST /users"
memkraft debug hypothesis "DB timeout" 
memkraft debug evidence "pool increase didn't help" --result contradicts
memkraft debug reject --reason "still timing out"
memkraft debug search-rejected "timeout"  # Learn from past failures
```

## CLI Reference

### Debug Commands
```
memkraft debug start "bug description"
memkraft debug hypothesis "missing null check" 
memkraft debug evidence "stack trace line 42" --result supports
memkraft debug reject --reason "wrong line"
memkraft debug confirm
memkraft debug status
memkraft debug history
memkraft debug search-rejected "regex"
```

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

**Feedback loop.** `--file-back` files search results back into entity timelines. Each query makes future queries richer — compound interest for memory.

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
├── originals/           # Captured verbatim — no paraphrasing
├── inbox/               # Quick capture before classification
├── tasks/               # Work-in-progress context
└── meetings/            # Briefs and notes
```

<br>

## Comparison

| | **MemKraft** | **Mem0** | **Letta** |
|---|:---:|:---:|:---:|
| **Storage** | Plain Markdown | Vector + Graph DB | DB-backed |
| **Dependencies** | Zero | Vector DB + API | DB + runtime |
| **Offline / git-friendly** | ✅ | ❌ | ❌ |
| Auto-extract (EN/KR/CN/JP) | ✅ | ✅ (LLM) | — |
| Agentic search | ✅ | — | — |
| Goal-weighted re-ranking | ✅ | — | — |
| Feedback loop | ✅ | — | — |
| Confidence levels | ✅ | — | — |
| Health check | ✅ | — | — |
| Conflict detection & resolution | ✅ | — | — |
| Source attribution | Required | — | — |
| Dream Cycle | ✅ | — | — |
| Memory tiers | ✅ | — | ✅ |
| Type-aware decay | ✅ | — | — |
| **Semantic search** | ❌ | ✅ | — |
| **Graph memory** | ❌ | ✅ | — |
| **Self-editing memory** | ❌ | — | ✅ |
| **Cost** | Free | Free tier + paid | Free |

**Choose MemKraft when:** you want portable, git-friendly, zero-dependency memory that works with any agent framework, offline, forever.

**Choose something else when:** you need semantic/vector search, graph traversal, or a full agent runtime with virtual context management.

<br>

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) — use it however you want.

---

## Changelog

### v0.4.0 (2026-04-13)

- **Debug Hypothesis Tracking (Debugging is Memory):** `mk.start_debug()` / `mk.log_hypothesis()` / `mk.log_evidence()` / `mk.reject_hypothesis()` / `mk.confirm_hypothesis()` / `mk.end_debug()` — full OBSERVE→HYPOTHESIZE→EXPERIMENT→CONCLUDE loop with persistent failure memory, 2-fail auto-switch warning, anti-pattern detection via `search_rejected_hypotheses()`, and feedback into entity timelines
- **CLI debug commands:** `memkraft debug start|hypothesis|evidence|reject|confirm|status|history|search-rejected`
- Tests: 198 → 259

### v0.3.0 (2026-04-13)

- **Query-to-Memory Feedback Loop:** `agentic-search --file-back` / `search --file-back` — search results auto-filed back to entity timelines (compound interest for memory)
- **Confidence Levels:** All facts support `verified` / `experimental` / `hypothesis` tags; `extract --confidence verified`; Dream Cycle warns about untagged facts; agentic-search re-ranking weights by confidence; conflict resolution via `--strategy confidence`
- **Memory Health Assertions:** `memkraft health-check` — 5 self-diagnostic assertions (source attribution, orphan facts, duplicates, inbox freshness, unresolved conflicts) with pass rate % and health score (A/B/C/D); auto-runs in Dream Cycle
- **Applicability Conditions:** `extract --when "condition" --when-not "condition"` — facts get `When:` / `When NOT:` metadata; agentic-search boosts results matching current context's applicability conditions
- **Python re-export:** `from memkraft import MemKraft` now works directly
- Tests: 158 → 198

### v0.2.0 (2026-04-12)

- **Goal-Weighted Reconstructive Memory (Conway SMS):** `agentic-search --context` — same query with different context produces different result rankings; memory-type-aware re-ranking with differential decay curves
- **Dialectic Synthesis:** Auto-detect contradictory facts during `extract`, tag with `[CONFLICT]`, generate `CONFLICTS.md` report, resolve via `dream --resolve-conflicts` or `resolve-conflicts` command
- **Memory Type Classification:** 8 memory types (identity, belief, preference, relationship, skill, episodic, routine, transient) with differential decay multipliers
- **Type-Aware Decay:** Identity memories decay 10x slower than routine memories
- Tests: 112 → 158

### v0.1.0 (2026-04-12)

- Initial release: extract, detect, decay, dedup, summarize, agentic search
- Entity tracking (track, update, brief, promote)
- Dream Cycle (7 health checks), cognify, retro
- Hybrid search (exact + IDF-weighted + fuzzy), agentic multi-hop search
- Zero dependencies — stdlib only

---

<div align="center">

**MemKraft** — *Agents don't learn. They search. Until now.*

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
| **Shubham Saboo's 6-agent system** | Multi-agent coordination with SOUL.md / MEMORY.md | [Article](https://x.com/Saboo_Shubham_/status/2042916549804077131) |
| **Karpathy llm-wiki** | Wiki-style structured knowledge for LLMs | [Tweet](https://x.com/karpathy/status/2042079355925164424) |

Thank you to all these creators for sharing their work openly.

</div>
