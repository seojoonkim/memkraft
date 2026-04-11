<div align="center">

<img src="docs/hero.jpg" alt="MemKraft — A cosmic forge where raw data is hammered into compound knowledge" width="100%">

# ⚒️ MemKraft

**The compound knowledge engine for AI agents.**

Raw conversations in. Compound expertise out. MemKraft forges your agent's memory<br>
from scattered fragments into an ever-growing, self-maintaining knowledge base.

[![PyPI](https://img.shields.io/pypi/v/memkraft?color=blue)](https://pypi.org/project/memkraft/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://pypi.org/project/memkraft/)
[![Zero dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)](https://pypi.org/project/memkraft/)

```bash
pipx install memkraft
```

</div>

---

## The problem

AI agents start every conversation from zero.

They search transcripts, re-read files, and reconstruct context on demand. Six months of conversations later, the agent still doesn't *know* anything — it just searches faster.

Every conversation is a dead end. No compound returns. Context windows fill up. Important facts get lost. And when an agent *does* recall something, you can't tell where it came from — was it from a reliable source, or a hallucination from three chats ago?

**Agents without compound memory don't learn. They search.**

## The solution

MemKraft forges **long-lived, compound knowledge** — structured, traceable, and self-maintaining.

```
  Raw Input ──▶ Extract ──▶ Classify ──▶ Forge ──▶ Compound Knowledge
       ▲                                                │
       └─────────── Brain-first recall ◄─────────────────┘
                          maintained by Dream Cycle ◀── nightly
```

Every fact has a source. Every entity has a timeline. Every night, Dream Cycle hammers out the dross. Your next conversation starts sharper than the last one.

---

## 60-second demo

```bash
# Initialize the forge
$ memkraft init
✅ MemKraft initialized at memory

# Auto-extract entities and facts from any text
$ memkraft extract "Simon Kim is the CEO of Hashed. Hashed is a VC in Seoul." \
    --source "X/@simonkim_nft"
[
  {"name": "Simon Kim", "type": "person", "context": "auto-detected", "source": "X/@simonkim_nft", "action": "created", "path": "entities/simon-kim.md"},
  {"entity": "Simon Kim", "fact": "the CEO of Hashed", "action": "appended"}
]

# Start tracking someone persistently
$ memkraft track "Simon Kim" --type person --source "X/@simonkim_nft"
✅ Tracking: memory/live-notes/simon-kim.md

# Update with new info
$ memkraft update "Simon Kim" --info "Open-sourced MemKraft under MIT" \
    --source "X/@simonkim_nft, 2026-04-10"
✅ Updated: memory/live-notes/simon-kim.md

# Promote to core memory (always in context)
$ memkraft promote "Simon Kim" --tier core
✅ Promoted 'Simon Kim' → core

# Get a meeting brief — all context in one place
$ memkraft brief "Simon Kim"

📋 Meeting Brief: Simon Kim
Generated: 2026-04-11

👤 Entity Info
   CEO of Hashed. VC based in Seoul.

🔄 Live Note
   Current State: CEO of Hashed, building MemKraft, the compound knowledge engine
   Recent Activity:
   - 2026-04-10 | Open-sourced MemKraft [Source: X/@simonkim_nft]

📅 Timeline
   - 2026-04-11 | Entity first detected [Source: Telegram]
   - 2026-04-10 | MemKraft MIT release [Source: X/@simonkim_nft]

🔓 Open Threads
   - [ ] Initial entity — enrichment needed

# Detect CJK entities — Chinese, Japanese, Korean out of the box
$ memkraft detect "马化腾和李彦宏讨论了人工智能" --dry-run
[
  {"name": "马化腾", "type": "person", "context": "auto-detected (Chinese)"},
  {"name": "李彦宏", "type": "person", "context": "auto-detected (Chinese)"}
]

# Process inbox — recommendation-only by default (no silent misclassification)
$ memkraft cognify
🧠 Cognify complete (recommend mode): 3 processed, 1 skipped
   → meeting-notes.md: decision (use --apply to move)
   → decision-001.md: decision (use --apply to move)
   → action-items.md: task (use --apply to move)

# Progressive Disclosure Query — 3 levels of token efficiency
$ memkraft query simon --level 1
  2026-04-11 live-notes/simon-kim.md
    - **2026-04-11** | Tracking started

$ memkraft query --level 2 --date 2026-04-11
  📄 live-notes/simon-kim.md
    # Simon Kim (Live Note)
    ## Tracking Config
    ## Current State
    ## Recent Activity
    ## Key Points

# Session Event Logging — structured audit trail
$ memkraft log --event "Deployed v0.2 to production" --tags deploy,release --importance high
📝 Logged: Deployed v0.2 to production

$ memkraft log --read
📋 Session events for 2026-04-11 (2 events):
  🔴 14:30:00 Deployed v0.2 to production [deploy,release]
  ⚪ 11:00:00 Bug fix applied [bug]

# Daily Retrospective — Well / Bad / Next
$ memkraft retro
🔄 Daily Retrospective — 2026-04-11
✅ Well (went well):
  • Deployed v0.2 to production
⚠️ Bad (issues):
  • (none)
➡️ Next (action items):
  • (none)
👥 Entities touched: MemKraft

# Distill decision candidates from events and notes
$ memkraft distill-decisions
📋 Decision candidates (1):
  [high] sessions/2026-04-11.jsonl: decided to use MemKraft over alternatives

# Track unresolved items across all memory
$ memkraft open-loops
🔓 Open Loops (2):
  [2026-04-11] live-notes/simon-kim.md: - [ ] Initial setup — enrichment needed
  [2026-04-10] decisions/stack.md: ⏳ pending team approval

# Build memory index for progressive disclosure
$ memkraft index
📇 Index built: 15 files → .memkraft/index.json

# Suggest missing wiki-links
$ memkraft suggest-links
🔗 Link suggestions (1):
  meetings/2026-04-10-hashed.md: add [[simon-kim]] — "CEO of Hashed, building MemKraft"

# Extract numeric/date facts to cross-domain registry
$ memkraft extract-facts "Revenue $5.3M, 85% growth, 42 employees, deadline 2026-06-30"
📊 Facts extracted (4):
  • $5.3M
  • 85%
  • 42 employees
  • 2026-06-30

# Brain-first lookup with sufficiency threshold
$ memkraft lookup "Simon" --brain-first
  [high] live-note: simon-kim
  (brain-first: stopped after 2 high-relevance results. Use --full for all.)

# Fuzzy search — find even when you don't remember the exact words
$ memkraft search "venture capital Seoul" --fuzzy
  [0.72] entities/simon-kim.md
     Simon Kim | CEO of Hashed. VC based in Seoul.
  [0.58] entities/hashed.md
     Hashed | VC firm in Seoul, blockchain-focused

# Backlinks — see who references whom
$ memkraft links "Simon Kim"
Backlinks to 'Simon Kim' (3):
  📎 entities/hashed.md
     ...CEO [[simon-kim]] founded Hashed in 2018...
  📎 decisions/seed-round.md
     ...introduced by [[simon-kim]]...
  📎 live-notes/memkraft.md
     ...[[simon-kim]] open-sourced MemKraft...

# Diff — what changed since last maintenance?
$ memkraft diff
Changes since last Dream Cycle (4):
  🆕 created: entities/simon-kim.md (2026-04-11 16:00)
  ✏️ modified: entities/hashed.md (2026-04-11 15:30)

# Dream Cycle — nightly auto-maintenance, catches facts without sources
$ memkraft dream --dry-run
🌙 Dream Cycle — 2026-04-11
   Mode: dry-run
   🔍 Scanning for incomplete source attributions...
      ⚠️ entities/hashed.md: timeline entry missing [Source: ...]
   🔍 Scanning for thin entity pages...
   🔍 Scanning for duplicate entities...
      ⚠️ Possible duplicate: simon-kim ↔ 김서준
   🔍 Scanning for inbox overdue items...
   🔍 Scanning for bloated pages (auto-compact)...
🌙 Dream Cycle complete: 3 total issues found
   Incomplete sources: 1
   Thin entities: 0
   Duplicate entities: 1
   Inbox overdue: 0
   Bloated pages: 1

# Capture raw text verbatim — no paraphrasing, no interpretation loss
$ echo "Simon: 'We're building the memory layer that agents actually need.'" \
    > memory/originals/simon-2026-04-11.md

# RESOLVER.md — where does new content go? The decision tree answers.
$ cat memory/RESOLVER.md
# RESOLVER — Classification Decision Tree
## Is it a person, company, or concept? → entities/
## Is it a decision with rationale? → decisions/
## Is it raw capture before processing? → inbox/ then cognify
## Is it verbatim text to preserve? → originals/
```

---

## How it works — technical design

### Zero-dependency philosophy

MemKraft runs on **Python 3.9+ with zero external dependencies**. No vector databases, no LLM API calls at runtime, no framework lock-in. The entire system is built from the standard library: `re` for pattern matching, `difflib` for fuzzy search, `json` for structured data, `pathlib` for file operations.

Why? Because memory should be **portable and permanent**. A Markdown file from 2026 is still readable in 2036. A vector embedding from a proprietary model may not even decode. When you `git push` your memory directory, you're backing up knowledge in its most durable form.

### Compiled Truth + Timeline (dual-layer entity model)

Every entity page is forged in two layers:

- **Compiled Truth** — the current state. Mutable, always rewritable. This is what an LLM reads first: role, affiliation, key context. When facts change, you re-forge this section.
- **Timeline** — an append-only log of every event, each tagged with `[Source: who, when, how]`. Never edited, only appended.

Why dual-layer? Because a single "current state" page silently overwrites history. Six months later, you can't answer "when did their role change?" or "who told us that?". The timeline is an audit trail — it makes every claim traceable. Compiled Truth makes it actionable. Together, they give you both *what we know now* and *how we got here*.

### Auto-extract: entity and fact detection

`memkraft extract` runs a multi-stage detection pipeline on any input text:

1. **English names** — regex for `Title Case + Title Case` patterns (2-word and 3-word), filtered against a common-word blocklist to avoid false positives like "The Company"
2. **Korean names** — Hangul syllable extraction with Korean particle stripping (조사 제거: 이, 을, 를, 은, 는, 에, 로...) and verb-suffix removal (했다, 한다, 해요, 됨...) to isolate proper nouns from sentence fragments
3. **Chinese names** — surname-first detection using a built-in dictionary of 100 Chinese surnames (王李张刘陈杨赵黄周吴...). Since Chinese characters lack word boundaries, the engine scans character runs and extracts 2–3 character sequences starting with a known surname
4. **Japanese names** — surname-matched detection against 85 Japanese surnames (田中, 佐藤, 鈴木, 高橋...), extracting surname + 1–2 character given name
5. **Fact extraction** — pattern matching for "X is/was/founded/leads Y" constructions in English and "X은/는/이/가 Y이다/다/했다" in Korean
6. **Stopword filtering** — 806 Korean/Chinese/Japanese stopwords loaded from `stopwords.json`, cached per session to avoid redundant I/O

All detected entities are de-duplicated and routed to `entities/` with auto-generated pages. Detected facts are appended to the relevant entity's Key Points or Timeline sections.

### Cognify: inbox → structured pages (recommendation-only by default)

The `cognify` command processes raw captures in `inbox/` and recommends routing to the right destination:

- **Decision** — if the text contains "decided", "decision", "chose", "agreed" → `decisions/`
- **Task** — if the text contains "todo", "task", "action item", "need to", "must" → `tasks/`
- **Entity** — if the text contains role words like "CEO", "CTO", "founder", "investor" → `entities/`
- **Entity** (default) — anything else lands in `entities/` for manual review

**By default, cognify only recommends** — it shows where each file should go without moving anything. Add `--apply` to actually route files. This prevents silent misclassification: you review the recommendations first, then apply them explicitly.

Files under 20 characters are skipped. `--dry-run` shows where everything would go. This is a heuristic classifier, not an LLM — it runs instantly, costs nothing, and works offline.

### Source Attribution: trust chain enforcement

Every fact in MemKraft carries a `[Source: who, when, how]` tag. The Dream Cycle specifically scans for timeline entries that lack source attribution and flags them. This isn't optional metadata — it's the difference between "Simon Kim is CEO of Hashed [Source: X/@simonkim_nft, 2026-04-10]" and "Simon Kim is CEO of Hashed" (wait, who said that?).

Why enforce this? Because LLMs hallucinate. When an agent retrieves a fact from memory, source attribution lets it judge reliability. Facts without sources are trust debts — they work until they don't. MemKraft makes the debt visible.

### Memory Tiers: explicit context window management

Every page has a tier label: **Core**, **Recall**, or **Archival**.

- **Core** — always relevant, always include in context. Live Notes default. The people and projects you're actively working with.
- **Recall** — searchable, include when explicitly relevant. Entity default. Background context that matters sometimes.
- **Archival** — historical, rarely accessed. Old decisions, former roles, completed projects.

Why tiers? Because LLM context windows are finite. Without explicit priority, you're either cramming everything in (expensive, noisy) or guessing what matters (lossy). Tiers give your agent a clear rule: load core, search recall, skip archival. `memkraft promote` lets you reclassify as priorities shift.

### Dream Cycle: automated memory maintenance

Run `memkraft dream` (or schedule it nightly). It performs six health checks:

1. **Daily note fallback** — auto-creates `memory/YYYY-MM-DD.md` if missing, ensuring there's always a daily note to write to.
2. **Incomplete source attributions** — scans every Timeline entry for missing `[Source: ...]` tags. Flags each one so you can add provenance.
3. **Thin entity pages** — flags any entity page under 300 bytes. These are placeholders that got created by `detect` or `extract` but never enriched.
4. **Duplicate entity detection** — normalizes entity slugs (lowercase, strips Korean particles) and flags potential duplicates like `simon-kim` ↔ `김서준` or `hashed` ↔ `해시드`.
5. **Overdue inbox items** — flags anything in `inbox/` older than 48 hours. If it's been sitting there for two days, it either needs to be cognified or deleted.
6. **Bloated pages (auto-compact)** — flags any page over 4KB. When Compiled Truth sections grow too long, they waste context window space. Inspired by [Recursive Language Models (Zhang et al., 2025)](https://arxiv.org/abs/2512.24601): selectively condensing context is more effective than expanding context windows.

After running, Dream Cycle writes a timestamp to `.memkraft/last-dream-timestamp`. This enables `memkraft diff` to show exactly what changed since the last maintenance pass.

### Fuzzy search: difflib-based relevance ranking

`memkraft search --fuzzy` uses Python's built-in `difflib.SequenceMatcher` — no vector embeddings, no embedding model, no API calls. It compares the query against every line in every memory file and the filename itself, keeping matches above a 0.3 similarity threshold, sorted by relevance score. Matched lines return ±3 lines of context as a snippet for efficient scanning.

Why not semantic search? Semantic search requires an embedding model, which means a dependency, an API key, and a running service. MemKraft's fuzzy search works offline, in CI, on a plane, with zero setup. For most "I know I wrote something about..." queries, it's good enough. When it isn't, you can always fall back to `grep`.

### Backlinks: wiki-style cross-references

MemKraft uses `[[entity-name]]` syntax (compatible with Obsidian, Logseq, and other Markdown tools) to create cross-references between pages. `memkraft links "Entity Name"` scans every `.md` file for references and returns:

- Which files reference the entity
- Context around the reference (40 characters on each side)

This gives you a live dependency graph without any database. When you're about to update an entity, backlinks tell you what else might be affected.

### RESOLVER.md: MECE classification tree

`RESOLVER.md` is a decision tree that lives in your memory directory. It defines mutually exclusive, collectively exhaustive rules for where new content should go. When Cognify routes an inbox item, or when you're manually deciding "does this go in entities/ or decisions/?", RESOLVER gives you an unambiguous answer.

The key property: **MECE** — every piece of knowledge has exactly one correct destination, and every destination is defined. This prevents the two most common memory diseases: duplicates (same person filed under three different slugs) and orphans (a page that doesn't belong anywhere).

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                        MemKraft                          │
│                The Compound Knowledge Engine              │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │  Extract  │─▶│ RESOLVER │─▶│ Classify │              │
│  │(auto-detect)│ │ (MECE   │─▶│ & Route  │              │
│  └──────────┘  │  tree)   │  └────┬─────┘              │
│  ┌──────────┐  └──────────┘       │                     │
│  │  Inbox    │──────▶ Cognify ────┘                     │
│  │ (capture) │     (recommend    │                     │
│  └──────────┘      by default)   │                     │
│                                     ▼                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Decisions │  │ Entities │  │Live Notes│              │
│  │ (why)     │  │ (who)    │  │ (track)  │              │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘              │
│        │             │             │                     │
│        └─────────────┼─────────────┘                     │
│                      ▼                                   │
│  ┌──────────────┐  ┌──────────────┐                     │
│  │ Dream Cycle  │  │   Sessions    │                     │
│  │ (auto-heal)  │  │  (JSONL log)  │                     │
│  │ • sources    │  │              │                     │
│  │ • thin pages  │  └──────┬───────┘                     │
│  │ • duplicates  │         │                              │
│  │ • inbox age   │         ▼                              │
│  │ • bloated     │  ┌──────────────┐                     │
│  │ • daily note  │  │  Retro &     │                     │
│  └──────┬───────┘  │  Distill      │                     │
│         │           └──────────────┘                     │
│         ▼                                                 │
│  ┌──────────────┐  ┌──────────────┐                     │
│  │    Diff      │  │  Open Loops  │                     │
│  │ (changes)    │  │  (unresolved)│                     │
│  └──────────────┘  └──────────────┘                     │
│                                                           │
│  ┌──────────────────────────────────────────────┐       │
│  │ Progressive Disclosure: query --level 1|2|3    │       │
│  │ Brain-first Lookup: entities → notes → ...    │       │
│  │ Memory Index: .memkraft/index.json            │       │
│  │ Source Attribution: [Source: who, when, how]  │       │
│  │ Memory Tiers: core | recall | archival         │       │
│  │ Backlinks: [[entity-name]]                    │       │
│  │ Fact Registry: cross-domain numeric index     │       │
│  └──────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────┘
```

### Memory structure

```
memory/
├── .memkraft/           # Internal state (index.json, Dream Cycle timestamps)
├── sessions/            # Structured event logs (YYYY-MM-DD.jsonl)
├── RESOLVER.md          # Classification decision tree (MECE)
├── TEMPLATES.md         # Page templates with tier labels
├── open-loops.md        # Unresolved items hub (auto-generated)
├── fact-registry.md    # Cross-domain numeric/date facts
├── YYYY-MM-DD.md       # Daily notes (auto-created by Dream Cycle)
├── entities/            # People, companies, concepts (Tier: recall)
├── live-notes/          # Persistent tracking targets (Tier: core)
├── decisions/           # Why we decided what we decided
├── originals/           # Captured verbatim — no paraphrasing
├── inbox/               # Quick capture before classification
├── tasks/               # Work-in-progress context
└── meetings/            # Briefs and notes
```

---

## Key features

| Feature | What it does | How it works |
|---------|-------------|-------------|
| **Auto-extract** | Pipe any text → entities + facts auto-detected and stored | Regex-based NER for EN (Title Case), KR (Hangul + particle stripping), CN (100 surnames), JP (85 surnames) + fact pattern matching |
| **Cognify** | Process inbox → structured pages. Recommendation-only by default. | Heuristic keyword classifier routes files to the right directory. `--apply` to actually move |
| **Progressive Disclosure** | 3-level query for token efficiency | Level 1: index scan (~50 tokens), Level 2: section headers, Level 3: full file. Filters: `--recent`, `--tag`, `--date` |
| **Session Logging** | Structured event audit trail | JSONL per day with tags, importance, entity, task, decision fields. `log --read` to review |
| **Daily Retrospective** | Well / Bad / Next — auto-generated from events + changes | Combines session events, inbox items, and file changes into structured retro |
| **Decision Distillation** | Scan events and notes for decision candidates | Matches EN (decided, chose, approved...) + KR (결정, 채택, 승인...) keywords |
| **Open Loop Tracking** | Find all unresolved/pending items across memory | Scans for pending, TODO, FIXME, [ ], ⏳ keywords → `open-loops.md` hub |
| **Memory Index** | Build `.memkraft/index.json` for fast scanning | Date, summary, tags, sections, file size per file — foundation for progressive disclosure |
| **Link Suggestions** | Suggest missing `[[wiki-links]]` based on entity names | Matches entity slugs against text not already wrapped in `[[]]` |
| **Fact Registry** | Extract numeric/date facts to cross-domain index | Currency, percentages, dates, quantities → `fact-registry.md` |
| **Brain-first lookup** | Search memory before the web with sufficiency threshold | Searches entities → live-notes → decisions → meetings → originals → inbox → tasks. Stops after ≥2 high-relevance results unless `--full` |
| **Live Notes** | Track people/companies persistently. Auto-update with new info | Dual-layer pages with auto-incrementing update count and timeline append |
| **Meeting Brief** | One command to compile everything before a meeting | Pulls entity info, live note state, recent timeline, open threads, related decisions, and generates a pre-meeting checklist |
| **Entity detection** | Auto-detect people in EN/KR/CN/JP text (regex-based) | 806 stopwords (533 KR + 250 CN + 23 JP), 100 CN surnames, 85 JP surnames, Korean particle/suffix stripping |
| **Source attribution** | Every fact tagged with `[Source: who, when, how]` | Enforced by Dream Cycle scans — facts without sources are flagged as trust debts |
| **Memory tiers** | Core / Recall / Archival — explicit context window priority | Labels on every page, `promote` command to reclassify as priorities shift |
| **Dream Cycle** | Nightly auto-maintenance | 6 checks: daily note fallback, incomplete sources, thin pages, duplicate entities, overdue inbox, bloated pages |
| **Diff tracking** | See what changed since last Dream Cycle | Compares file mtimes against `.memkraft/last-dream-timestamp`, reports created/modified |
| **Fuzzy search** | Find even when you don't remember the exact name | `difflib.SequenceMatcher` with 0.3 threshold + ±3 line snippets, zero dependencies, works offline |
| **Agentic search** | Multi-step search: decompose query → search → traverse links → re-rank | Decomposes complex queries, follows [[wiki-links]] across pages, re-ranks by tier/recency/type, auto-expands if insufficient results |
| **Backlinks** | See every page that references an entity | Scans all `.md` files for `[[entity-name]]` patterns, returns file + context excerpt |
| **RESOLVER.md** | MECE classification tree — prevents duplicates and misfiling | Decision tree that makes every routing decision unambiguous |
| **Originals/** | Capture ideas verbatim. No paraphrasing, no interpretation loss | Raw capture before any processing — the source of truth for interpretation |
| **Plain Markdown** | Zero lock-in. Read it, edit it, grep it, git it | No proprietary formats, no database, no API required to read your own memory |

---

## Comparison

| | **MemKraft** | **Mem0** | **Letta** | **GBrain** | **Rowboat** |
|---|---|---|---|---|---|
| Knowledge structure | Compiled Truth + Timeline | Graph + vector | Tiered (core/recall/archival) | Compiled Truth + Timeline | Obsidian vault |
| Auto-extract | ✅ | ✅ | — | — | — |
| Cognify pipeline | ✅ | — | — | — | — |
| Progressive disclosure | ✅ | — | — | — | — |
| Session logging | ✅ | — | — | — | — |
| Daily retrospective | ✅ | — | — | — | — |
| Decision distillation | ✅ | — | — | — | — |
| Open loop tracking | ✅ | — | — | — | — |
| Memory index | ✅ | — | — | — | — |
| Link suggestions | ✅ | — | — | — | — |
| Fact registry | ✅ | — | — | — | — |
| Entity detection | ✅ (EN/KR/CN/JP) | ✅ (LLM) | — | — | — |
| Live tracking | ✅ | — | — | — | ✅ |
| Meeting prep | ✅ | — | — | — | ✅ |
| Source attribution | ✅ Required | — | — | ✅ | — |
| Dream Cycle | ✅ | — | — | — | — |
| Memory tiers | ✅ | — | ✅ | — | — |
| Diff tracking | ✅ | — | — | — | — |
| Fuzzy search | ✅ | ✅ (vector) | — | — | — |
| Agentic search | ✅ | — | — | — | — |
| Semantic search | — | ✅ | — | — | — |
| Backlinks | ✅ | — | — | — | — |
| Memory resolver | ✅ | — | — | — | — |
| Originals capture | ✅ | — | — | — | — |
| Auto-compact | ✅ | — | — | — | — |
| **Memory decay** | ✅ **R14** | — | — | — | — |
| **Fact dedup** | ✅ **R14** | — | — | — | — |
| **Auto-summarize** | ✅ **R14** | — | ✅ | — | — |
| Self-editing memory | — | — | ✅ | — | — |
| Graph memory | — | ✅ | — | — | — |
| Virtual context mgmt | — | — | ✅ | — | — |
| Offline / git-friendly | ✅ | — | — | ✅ | ✅ |
| Zero dependencies | ✅ | — | — | ✅ | — |
| Framework | Framework-agnostic | API-first (Python/JS) | Agent framework | Claude-specific | Desktop app |
| Storage | Plain Markdown | Vector DB + graph DB | DB-backed | Plain Markdown | Plain Markdown |
| Cost to run | Free | Free tier + paid | Free | Free | Free |

**Different design priorities:**

- **Mem0** — strong at automatic memory extraction and semantic graph search. Best when you need API-first integration with vector retrieval and graph traversal. MemKraft takes the auto-extraction idea but keeps everything in readable, git-friendly Markdown instead of opaque vector embeddings.
- **Letta** (MemGPT) — pioneered tiered memory and virtual context management within a full agent framework. Best when you want a complete agent runtime with automatic context paging. MemKraft adopts tier labels as a lightweight, framework-agnostic convention without requiring a runtime.
- **GBrain** — the compiled-truth + timeline model was a direct inspiration. Best for Claude-specific workflows where the AI directly maintains knowledge pages. MemKraft generalizes it to be framework-agnostic and adds Dream Cycle, auto-extraction, and cognify.
- **Rowboat** — persistent live-tracking and meeting briefs are essential ideas. Best as an Obsidian desktop app for human-in-the-loop workflows. MemKraft incorporates them into a CLI-first, programmable workflow that agents can call directly.

MemKraft was originally built as a production memory system for a multi-agent team, then sharpened by incorporating ideas from each of these projects. The result: **a complete, transparent, self-maintaining compound knowledge engine** — tested in production, not just designed in theory.

---

## Installation

### Install

```bash
pipx install memkraft
```

`pipx` installs CLI tools in isolated virtualenvs — no dependency conflicts, no global pollution. One command to install, one to uninstall (`pipx uninstall memkraft`).

Don't have pipx?
```bash
brew install pipx     # macOS
pipx ensurepath
```

### Run without installing

```bash
pipx run memkraft brief
```

### From source

```bash
git clone https://github.com/seojoonkim/memkraft.git
cd memkraft
pipx install .
```

**Requirements:** Python 3.9+. No other dependencies.

---

## Quick Start

```bash
# 1. Initialize & extract — from zero to compound knowledge in 30 seconds
memkraft init
memkraft extract "Simon Kim is the CEO of Hashed in Seoul." --source "news"

# 2. Track & update — persistent living documents
memkraft track "Simon Kim" --type person --source "X/@simonkim_nft"
memkraft update "Simon Kim" --info "Launched MemKraft" --source "X/@simonkim_nft"

# 3. Search & brief — find anything, prep for meetings
memkraft search "venture capital" --fuzzy
memkraft brief "Simon Kim"
memkraft agentic-search "who is the CEO of Hashed"  # decompose → traverse → re-rank

# 4. Ingest & classify — inbox → structured pages (safe by default)
memkraft cognify          # recommend-only; add --apply to move files
memkraft detect "Jack Ma and 马化腾 discussed AI" --dry-run

# 5. Maintain & audit — Dream Cycle keeps memory healthy
memkraft dream --dry-run  # nightly: sources, duplicates, bloated pages, decay
memkraft diff              # what changed since last Dream Cycle?

# 6. Everything else — one-command access
memkraft log --event "Deployed v0.2" --tags deploy --importance high
memkraft retro             # daily retrospective
memkraft lookup "Simon" --brain-first  # brain-first recall
memkraft links "Simon Kim" # backlinks
memkraft open-loops        # unresolved items
memkraft list              # what you're tracking
```

All 20+ commands are available from day one. The 6 patterns above cover 90% of daily use.

### Configuration

```bash
# Set memory directory (default: ./memory)
export MEMKRAFT_DIR=/path/to/your/memory
```

---

## Design philosophy

> An agent without compound memory answers from stale context every time.<br>
> An agent with MemKraft gets sharper with every conversation.

### Six principles

1. **Memory compounds** — each conversation builds on all prior ones, not just the last window. The hundredth conversation should be the best-informed one.
2. **Structure enforces quality** — RESOLVER prevents duplication, Source Attribution enforces trustworthiness, Tiers prioritize what matters. Good structure makes bad data visible.
3. **Maintenance is automated** — Dream Cycle keeps memory healthy without manual effort. Memory that requires constant human curation will rot.
4. **Knowledge is portable** — plain Markdown, zero dependencies, works with any agent framework. If MemKraft disappears tomorrow, your memory is still readable.
5. **Provenance is non-negotiable** — every fact traces back to a source. Facts without sources are trust debts, and Dream Cycle makes sure you see them.
6. **Context is finite** — LLM context windows have hard limits. Memory tiers ensure the right information fills that space, and bloated pages get flagged for compaction. Inspired by [Recursive Language Models (Zhang et al., 2025)](https://arxiv.org/abs/2512.24601), which demonstrates that decomposing and selectively retrieving context dramatically outperforms brute-force long-context approaches.

The goal: a system where **knowledge begets knowledge** — where the marginal cost of each new insight decreases because the foundation keeps growing. MemKraft is the engine that forges that compound loop.

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT

---

<div align="center">

**MemKraft** — *Forge your memory. Compound your knowledge.*

</div>
