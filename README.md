<div align="center">

# 🧠⚒️ MemCraft

**The ultimate compound knowledge system for AI agents.**

From first conversation to compounding expertise — MemCraft gives your agent<br>
a memory that grows, self-maintains, and gets smarter over time.

[![PyPI](https://img.shields.io/pypi/v/memcraft?color=blue)](https://pypi.org/project/memcraft/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://pypi.org/project/memcraft/)

```bash
pip install memcraft
```

</div>

---

## The problem

AI agents start every conversation from scratch.

They search transcripts, re-read files, and reconstruct context on demand. Six months of conversations later, the agent still doesn't *know* anything — it just searches faster.

Every conversation is a dead end. No compound returns.

## The solution

MemCraft gives your agent **long-lived, compound knowledge** — structured, traceable, and self-maintaining.

```
  Conversation ──▶ Extract ──▶ Classify ──▶ Track ──▶ Compound
       ▲                                                  │
       └──────────── Brain-first lookup ◄─────────────────┘
                          maintained by Dream Cycle ◀── nightly
```

Every fact has a source. Every entity has a timeline. Every night, Dream Cycle keeps it all healthy. Your next conversation starts smarter than the last one.

---

## 60-second demo

```bash
# Initialize memory structure
$ memcraft init
✅ MemCraft initialized at memory

# Auto-extract entities and facts from any text
$ memcraft extract "Simon Kim is the CEO of Hashed. Hashed is a crypto VC in Seoul." \
    --source "X/@simonkim_nft"
[
  {"name": "Simon Kim", "type": "person", "action": "created"},
  {"name": "Hashed", "type": "person", "action": "created"},
  {"entity": "Simon Kim", "fact": "the CEO of Hashed", "action": "appended"}
]

# Start tracking someone
$ memcraft track "Simon Kim" --type person --source "X/@simonkim_nft"
✅ Tracking: memory/live-notes/simon-kim.md

# Update with new info
$ memcraft update "Simon Kim" --info "Launched VibeKai, a vibe coding education platform" \
    --source "X/@simonkim_nft, 2026-04-10"
✅ Updated: memory/live-notes/simon-kim.md

# Promote to core memory (always in context)
$ memcraft promote "Simon Kim" --tier core
✅ Promoted 'Simon Kim' → core

# Get a meeting brief
$ memcraft brief "Simon Kim"

📋 Meeting Brief: Simon Kim
Generated: 2026-04-11

👤 Entity Info
   CEO of Hashed. Crypto VC based in Seoul.

🔄 Live Note
   Current State: CEO of Hashed, building VibeKai and MemCraft
   Recent Activity:
   - 2026-04-10 | Launched VibeKai [Source: X/@simonkim_nft]

📅 Timeline
   - 2026-04-11 | Entity first detected [Source: Telegram]
   - 2026-04-10 | VibeKai launch [Source: X/@simonkim_nft]

🔓 Open Threads
   - [ ] Initial entity — enrichment needed

# Detect CJK entities out of the box
$ memcraft detect "马化腾和李彦宏讨论了人工智能" --no-llm --dry-run
[
  {"name": "马化腾", "type": "person", "context": "auto-detected (Chinese)"},
  {"name": "李彦宏", "type": "person", "context": "auto-detected (Chinese)"}
]

# Process inbox into structured pages
$ memcraft cognify --dry-run
🧠 Cognify complete: 3 processed, 1 skipped
   would route: meeting-notes.md → entity
   would route: decision-001.md → decision
   would route: action-items.md → task

# Fuzzy search across all memory
$ memcraft search "crypto venture capital Seoul" --fuzzy
  [0.72] entities/simon-kim.md
  [0.58] entities/hashed.md

# Show backlinks to an entity
$ memcraft links "Simon Kim"
Backlinks to 'Simon Kim' (3):
  📎 entities/hashed.md
     ...CEO [[simon-kim]] founded Hashed in 2018...
  📎 decisions/seed-round.md
     ...introduced by [[simon-kim]]...
  📎 live-notes/vibekai.md
     ...[[simon-kim]] launched VibeKai...

# See what changed since last Dream Cycle
$ memcraft diff
Changes since last Dream Cycle (4):
  🆕 created: entities/simon-kim.md (2026-04-11 16:00)
  ✏️ modified: entities/hashed.md (2026-04-11 15:30)

# Run nightly maintenance
$ memcraft dream --dry-run
🌙 Dream Cycle — 2026-04-11
   🔍 Scanning for incomplete source attributions...
   🔍 Scanning for thin entity pages...
   🔍 Scanning inbox for overdue items...
🌙 Dream Cycle complete: 2 issues found
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                        MemCraft                          │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │  Extract  │─▶│ RESOLVER │─▶│ Classify │              │
│  │(auto-detect)│ │ (MECE   │─▶│ & Route  │              │
│  └──────────┘  │  tree)   │  └────┬─────┘              │
│  ┌──────────┐  └──────────┘       │                     │
│  │  Inbox    │──────▶ Cognify ────┘                     │
│  │ (capture) │        (process)                          │
│  └──────────┘                                           │
│                                     ▼                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Decisions │  │ Entities │  │Live Notes│              │
│  │ (why)     │  │ (who)    │  │ (track)  │              │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘              │
│        │             │             │                     │
│        └─────────────┼─────────────┘                     │
│                      ▼                                   │
│              ┌──────────────┐                            │
│              │  Dream Cycle │ ◀── nightly maintenance    │
│              │  (auto-heal) │                            │
│              └──────────────┘                            │
│                      │                                   │
│                      ▼                                   │
│              ┌──────────────┐                            │
│              │    Diff      │ ◀── change tracking        │
│              └──────────────┘                            │
│                                                          │
│  ┌─────────────────────────────────────────────┐        │
│  │ Source Attribution: [Source: who, when, how] │        │
│  │ Memory Tiers: core | recall | archival       │        │
│  │ Backlinks: [[entity-name]]                   │        │
│  └─────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────┘
```

### Memory structure

```
memory/
├── .memcraft/           # Internal state (Dream Cycle timestamps, etc.)
├── RESOLVER.md          # Classification decision tree
├── TEMPLATES.md         # Page templates
├── entities/            # People, companies, concepts
├── live-notes/          # Persistent tracking targets
├── decisions/           # Why we decided what we decided
├── originals/           # Captured verbatim — no paraphrasing
├── inbox/               # Quick capture before classification
├── tasks/               # Work-in-progress context
└── meetings/            # Briefs and notes
```

### Compiled Truth + Timeline

Every entity uses a **dual-layer structure**:

- **Compiled Truth** — current state, always rewritable, always up-to-date
- **Timeline** — append-only historical record with source attribution

The top half is *what we know now*. The bottom half is *how we got here*. Six months later, you can trace any fact back to its origin.

### Memory Tiers

Every page has a tier label:

- **Core** — always relevant, always in context (Live Notes default)
- **Recall** — searchable, referenced when needed (Entity default)
- **Archival** — historical, rarely accessed

Promote or demote with `memcraft promote "Name" --tier core`.

---

## Key features

| Feature | What it does |
|---------|-------------|
| **Auto-extract** | Pipe any text → entities + facts auto-detected and stored |
| **Cognify** | Process inbox → structured pages. One command. |
| **Brain-first lookup** | Search memory before the web. `memory → grep → web` |
| **Live Notes** | Track people/companies persistently. Auto-update with new info |
| **Meeting Brief** | One command to compile everything before a meeting |
| **Entity detection** | Auto-detect people in EN/KR/CN/JP text (regex + LLM) |
| **Source attribution** | Every fact tagged with `[Source: who, when, how]`. No source = not trustworthy |
| **Memory tiers** | Core / Recall / Archival — explicit priority for context windows |
| **Dream Cycle** | Nightly auto-maintenance: fix thin pages, flag missing sources, prune inbox |
| **Diff tracking** | See what changed since last Dream Cycle |
| **Fuzzy search** | Find even when you don't remember the exact name |
| **Backlinks** | See every page that references an entity |
| **RESOLVER.md** | MECE classification tree — prevents duplicates and misfiling |
| **Originals/** | Capture ideas verbatim. No paraphrasing, no interpretation loss |
| **Plain Markdown** | Zero lock-in. Read it, edit it, grep it, git it |

---

## Comparison

| | **MemCraft** | **Mem0** | **Letta** | **GBrain** | **Rowboat** |
|---|---|---|---|---|---|
| Knowledge structure | Compiled Truth + Timeline | Graph + vector | Tiered (core/recall/archival) | Compiled Truth + Timeline | Obsidian vault |
| Auto-extract | ✅ | ✅ | ❌ | ❌ | ❌ |
| Cognify pipeline | ✅ | ❌ | ❌ | ❌ | ❌ |
| Entity detection | ✅ (EN/KR/CN/JP) | ✅ (LLM extraction) | ❌ | ❌ | ❌ |
| Live tracking | ✅ | ❌ | ❌ | ❌ | ✅ |
| Meeting prep | ✅ | ❌ | ❌ | ❌ | ✅ |
| Source attribution | ✅ Required | ❌ | ❌ | ✅ | ❌ |
| Dream Cycle | ✅ | ❌ | ❌ | ❌ | ❌ |
| Memory tiers | ✅ | ❌ | ✅ | ❌ | ❌ |
| Diff tracking | ✅ | ❌ | ❌ | ❌ | ❌ |
| Fuzzy search | ✅ | ✅ (vector) | ❌ | ❌ | ❌ |
| Backlinks | ✅ | ❌ | ❌ | ❌ | ❌ |
| Memory resolver | ✅ | ❌ | ❌ | ❌ | ❌ |
| Originals capture | ✅ | ❌ | ❌ | ❌ | ❌ |
| Self-editing memory | ❌ | ❌ | ✅ | ❌ | ❌ |
| Graph memory | ❌ | ✅ | ❌ | ❌ | ❌ |
| Virtual context mgmt | ❌ | ❌ | ✅ | ❌ | ❌ |
| Offline / git-friendly | ✅ | ❌ | ❌ | ✅ | ✅ |
| Framework | Framework-agnostic | API-first (Python/JS) | Agent framework | Claude-specific | Desktop app |
| Storage | Markdown | Vector DB + graph DB | DB-backed | Markdown | Markdown |
| Cost to run | Free | Free tier + paid | Free | Free | Free |

**What MemCraft absorbs from each:**

- **Mem0** — auto memory extraction from text. But Mem0 trades transparency for convenience: vector embeddings you can't read, graph edges you can't edit. MemCraft keeps everything in readable Markdown.
- **Letta** (MemGPT) — tiered memory is a powerful idea (core/recall/archival). MemCraft adopts explicit tier labels without the complexity of virtual context paging.
- **GBrain** — the compiled-truth model was a direct inspiration. MemCraft adopts it and adds Dream Cycle, Source Attribution, auto-extraction, and more.
- **Rowboat** — persistent live-tracking is essential. MemCraft incorporates it alongside meeting briefs and resolution logic.

MemCraft was built as a production memory system for a multi-agent team, then refined by absorbing the best ideas from each of these projects. The result: **the most complete, transparent, self-maintaining compound knowledge system available** — tested in production, not just designed in theory.

---

## Installation

```bash
pip install memcraft
```

From source:

```bash
git clone https://github.com/seojoonkim/memcraft.git
cd memcraft
pip install -e .
```

---

## Quick Start

```bash
# 1. Initialize
memcraft init

# 2. Auto-extract from any text
memcraft extract "Simon Kim is the CEO of Hashed. Hashed is a crypto VC in Seoul." --source "news"

# 3. Start tracking
memcraft track "Simon Kim" --type person --source "X/@simonkim_nft"

# 4. Update with new info
memcraft update "Simon Kim" --info "CEO of Hashed, launched VibeKai" --source "X/@simonkim_nft"

# 5. Promote to core memory
memcraft promote "Simon Kim" --tier core

# 6. Prep for a meeting
memcraft brief "Simon Kim"

# 7. Detect entities in text
memcraft detect "Jack Ma and 马化腾 discussed AI" --source "news"

# 8. Process inbox
memcraft cognify

# 9. Search memory
memcraft search "crypto venture capital" --fuzzy

# 10. Check backlinks
memcraft links "Simon Kim"

# 11. See changes
memcraft diff

# 12. Nightly maintenance
memcraft dream --dry-run

# 13. See what you're tracking
memcraft list
```

### Configuration

```bash
# Set memory directory (default: ./memory)
export MEMCRAFT_DIR=/path/to/your/memory
```

---

## Philosophy

> An agent without compound memory answers from stale context every time.<br>
> An agent with it gets smarter with every conversation.

Four principles:

1. **Memory compounds** — each conversation builds on all prior ones, not just the last window
2. **Structure enforces quality** — RESOLVER prevents duplication, Source Attribution enforces trustworthiness, Tiers prioritize what matters
3. **Maintenance is automated** — Dream Cycle keeps memory healthy without manual effort
4. **Knowledge is portable** — plain Markdown, no lock-in, works with any agent framework

The goal: a system where **knowledge begets knowledge** — where the marginal cost of each new insight decreases because the foundation keeps growing. MemCraft is the engine that makes that happen.

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT

---

<div align="center">

**MemCraft** — *Forge your memory. Compound your knowledge.*

</div>