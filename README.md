<div align="center">

# 🧠⚒️ MemCraft

**Compound knowledge for AI agents.**

Every conversation your agent has is a deposit. MemCraft turns those deposits into compounding returns.

[![PyPI](https://img.shields.io/pypi/v/memcraft?color=blue)](https://pypi.org/project/memcraft/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://pypi.org/project/memcraft/)

```bash
pip install memcraft
```

</div>

---

## The problem

AI agents start every conversation from scratch. They search transcripts, re-read files, and reconstruct context on demand. Six months of conversations, and the agent still doesn't *know* anything.

It's like meeting the same person every day and reintroducing yourself each time.

## The solution

MemCraft gives your agent **long-lived, compound knowledge** — structured, traceable, and self-maintaining.

```
Conversation → Capture → Classify → Track → Compound
     ↑                                        │
     └─────────── Brain-first lookup ─────────┘
```

Every fact has a source. Every entity has a timeline. Every night, Dream Cycle keeps it all healthy. Next conversation starts smarter than the last one.

---

## How it works

### Initialize

```bash
$ memcraft init
✅ MemCraft initialized at memory
   Directories: entities/, live-notes/, decisions/, originals/, inbox/, tasks/, meetings/
   Files: RESOLVER.md, TEMPLATES.md
```

### Track

```bash
$ memcraft track "Garry Tan" --type person --source "X/@garrytan"
✅ Tracking: memory/live-notes/garry-tan.md
```

### Update

```bash
$ memcraft update "Garry Tan" --info "Open-sourced GBrain under MIT" --source "X/@garrytan, 2026-04-10"
✅ Updated: memory/live-notes/garry-tan.md
   📌 garry-tan (updates: 2, last: 2026-04-10)
```

### Meeting Brief

```bash
$ memcraft brief "Garry Tan"

📋 Meeting Brief: Garry Tan
Generated: 2026-04-11

👤 Entity Info
   YC CEO. Open-sourced GBrain, a knowledge graph system.

🔄 Live Note
   Current State: CEO of Y Combinator, advocates compound knowledge systems
   Recent Activity:
   - 2026-04-10 | Open-sourced GBrain under MIT [Source: X/@garrytan]

📅 Timeline
   - 2026-04-11 | Entity first detected [Source: Simon, Telegram]
   - 2026-04-10 | GBrain MIT license release [Source: X/@garrytan]

🔓 Open Threads
   - [ ] Initial entity — enrichment needed

✅ Pre-Meeting Checklist
   - [ ] Verify latest activity
   - [ ] Review open threads
   - [ ] Check related decisions
```

### Detect entities (CJK supported)

```bash
$ memcraft detect "马化腾和李彦宏讨论了人工智能" --no-llm --dry-run
[
  {"name": "马化腾", "type": "person", "context": "auto-detected (Chinese)"},
  {"name": "李彦宏", "type": "person", "context": "auto-detected (Chinese)"}
]

$ memcraft detect "김서준이 Hashed에서 발표했다" --no-llm --dry-run
[
  {"name": "김서준", "type": "person", "context": "auto-detected (Korean)"}
]
```

### Dream Cycle (nightly maintenance)

```bash
$ memcraft dream --dry-run
🌙 Dream Cycle — 2026-04-11
   Mode: dry-run
   🔍 Scanning for incomplete source attributions...
   🔍 Scanning for thin entity pages...
   🔍 Scanning inbox for overdue items...

🌙 Dream Cycle complete: 2 total issues found
   Incomplete sources: 0
   Thin entities: 1
   Inbox overdue: 1
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                        MemCraft                          │
│                                                          │
│  ┌─────────┐   ┌──────────┐   ┌──────────┐             │
│  │  Inbox   │──▶│ RESOLVER │──▶│ Classify │             │
│  │ (capture)│   │ (MECE    │   │ & Route  │             │
│  └─────────┘   │  tree)   │   └────┬─────┘             │
│                 └──────────┘        │                    │
│                                     ▼                    │
│  ┌─────────┐   ┌──────────┐   ┌──────────┐             │
│  │ Decisions│   │ Entities │   │Live Notes│             │
│  │ (why)    │   │ (who)    │   │ (track)  │             │
│  └────┬────┘   └────┬─────┘   └────┬─────┘             │
│       │              │              │                    │
│       └──────────────┼──────────────┘                    │
│                      ▼                                   │
│              ┌──────────────┐                            │
│              │  Dream Cycle │ ◀── nightly maintenance    │
│              │  (auto-heal) │                            │
│              └──────────────┘                            │
│                                                          │
│  ┌─────────────────────────────────────────────┐        │
│  │ Source Attribution: [Source: who, when, how] │        │
│  └─────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────┘
```

### Memory structure

```
memory/
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

This is the key insight: the top half is *what we know now*, the bottom half is *how we got here*. Six months later, you can trace any fact back to its origin.

---

## Key features

| Feature | What it does |
|---------|-------------|
| **Brain-first lookup** | Search memory before the web. `memory_search → grep → web_search` |
| **Live Notes** | Track people/companies persistently. Auto-update with new info |
| **Meeting Brief** | One command to compile everything before a meeting |
| **Entity detection** | Auto-detect people in EN/KR/CN/JP text (regex + LLM) |
| **Source attribution** | Every fact tagged with `[Source: who, when, how]`. No source = not trustworthy |
| **Dream Cycle** | Nightly auto-maintenance: fix thin pages, flag missing sources, prune inbox |
| **RESOLVER.md** | MECE classification tree — prevents duplicates and misfiling |
| **Originals/** | Capture ideas verbatim. No paraphrasing, no interpretation loss |
| **Plain Markdown** | Zero lock-in. Read it, edit it, grep it, git it |

---

## Comparison

| | **MemCraft** | **GBrain** | **Rowboat** |
|---|---|---|---|
| Knowledge structure | Compiled Truth + Timeline | Compiled Truth + Timeline | Obsidian vault |
| Entity detection | Auto (regex + LLM, CJK) | Manual | Auto (email/calendar) |
| Live tracking | ✅ | ❌ | ✅ |
| Meeting prep | ✅ | ❌ | ✅ |
| Source attribution | ✅ Required | ✅ | ❌ |
| Dream Cycle | ✅ | ❌ | ❌ |
| Memory resolver | ✅ | ❌ | ❌ |
| Originals capture | ✅ | ❌ | ❌ |
| CJK support | ✅ | ❌ | ❌ |
| Framework | Framework-agnostic | Claude-specific | Desktop app |
| Storage | Markdown | Markdown | Markdown |

MemCraft was originally built as an operational memory system for a multi-agent team, then sharpened by incorporating the best ideas from [GBrain](https://github.com/garrytan/gbrain) (Garry Tan's compiled-truth model) and [Rowboat](https://github.com/rowboatlabs/rowboat) (persistent live-tracking). The result is a system that's been tested in production — not just designed in theory.

---

## Installation

```bash
pip install memcraft
```

### From source

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

# 2. Start tracking
memcraft track "Garry Tan" --type person --source "X/@garrytan"

# 3. Update with new info
memcraft update "Garry Tan" --info "YC CEO, GBrain creator" --source "X/@garrytan, 2026-04-10"

# 4. Prep for a meeting
memcraft brief "Garry Tan"

# 5. Detect entities in text
memcraft detect "Jack Ma and 马化腾 discussed AI" --source "news"

# 6. Nightly maintenance
memcraft dream --dry-run

# 7. See what you're tracking
memcraft list
```

### Environment variable

```bash
# Set memory directory (default: ./memory)
export MEMCRAFT_DIR=/path/to/your/memory
```

---

## Philosophy

> An agent without compound memory answers from stale context every time. An agent with it gets smarter with every conversation.

MemCraft is built on four principles:

1. **Memory compounds** — each conversation builds on all prior ones, not just the last window
2. **Structure enforces quality** — RESOLVER prevents duplication, Source Attribution enforces trustworthiness
3. **Maintenance is automated** — Dream Cycle keeps memory healthy without manual effort
4. **Knowledge is portable** — plain Markdown, no lock-in, works with any agent framework

The goal isn't just smarter conversations. It's a system where **knowledge begets knowledge** — where the marginal cost of each new insight decreases over time because the foundation keeps growing.

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT

---

<div align="center">

**MemCraft** — *Forge your memory. Compound your knowledge.*

</div>
