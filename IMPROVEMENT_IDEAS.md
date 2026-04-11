# MemKraft Improvement Ideas

> Researched: 2026-04-11 | Source: Mem0, Cognee, Zep, Letta (MemGPT), GBrain, Rowboat

---

## 🔍 Related Projects Analysis

### 1. Mem0 (mem0ai/mem0) — 25k+ ⭐
**Link:** https://github.com/mem0ai/mem0
**Core:** Universal memory layer for AI agents. API-first, multi-level memory (User/Session/Agent state).
**Strengths:**
- Auto memory extraction from conversations (no manual input needed)
- Graph memory with relationship edges
- Multi-platform SDK (Python + JS)
- Hosted + self-hosted options
- +26% accuracy over OpenAI Memory on LOCOMO benchmark
- 90% fewer tokens than full-context
**MemKraft learns:** Auto-extraction from conversations is powerful. But Mem0 trades transparency (vector DB, opaque graph edges) for convenience.
**Absorbable ideas:** `memkraft extract` — auto-extract entities/facts from conversation text using regex + LLM, write directly to Markdown.

### 2. Cognee (topoteretes/cognee) — 3k+ ⭐
**Link:** https://github.com/topoteretes/cognee
**Core:** Knowledge engine that combines vector search + graph databases + cognitive science. Ingest → Cognify → Search.
**Strengths:**
- Unified ingestion pipeline (any format → knowledge graph)
- Graph + vector hybrid search
- Ontology grounding (structured knowledge layer)
- Cross-agent knowledge sharing
- OpenClaw plugin already exists!
**MemKraft learns:** The ingest → process → query pipeline is elegant. Ontology grounding prevents knowledge drift.
**Absorbable ideas:** `memkraft cognify` — process raw inbox items into structured entity/decision pages. `memkraft search` — hybrid grep + semantic search.

### 3. Zep (getzep/zep) — 3k+ ⭐
**Link:** https://github.com/getzep/zep
**Core:** End-to-end context engineering platform. Temporal knowledge graph, sub-200ms retrieval.
**Strengths:**
- Temporal knowledge graph (facts + how they change over time)
- Relationship-aware context assembly
- Multi-source: chat history, business data, documents, events
- Sub-200ms latency for production use
- Auto-extracts relationships
**MemKraft learns:** Temporal knowledge graph is exactly what MemKraft's Timeline does, but Zep automates the "how facts change" tracking. This is a natural evolution for MemKraft.
**Absorbable ideas:** Fact change tracking — when an entity's state changes, auto-generate a "state transition" entry in timeline.

### 4. Letta / MemGPT (letta-ai/letta) — 15k+ ⭐
**Link:** https://github.com/letta-ai/letta
**Core:** Tiered memory system (core/recall/archival). Virtual context management. LLM as OS.
**Strengths:**
- Three-tier memory: core (always in context), recall (searchable), archival (long-term)
- Self-editing memory (agent modifies its own memory)
- Virtual context paging (unbounded context through intelligent swapping)
- Research-backed (MemGPT paper)
**MemKraft learns:** Tiered memory is a powerful abstraction. MemKraft's Compiled Truth ≈ core, Timeline ≈ recall, originals/ ≈ archival. Making this explicit could improve UX.
**Absorbable ideas:** Explicit memory tier labeling. `memkraft promote` — move info from timeline to compiled truth.

### 5. GBrain (garrytan/gbrain) — 2k+ ⭐
**Link:** https://github.com/garrytan/gbrain
**Core:** Compiled Truth + Timeline for personal knowledge. Claude-specific.
**Strengths:**
- Compiled Truth + Timeline dual-layer model
- Simple, human-readable Markdown
- Brain-first lookup pattern
**MemKraft learns:** Already adopted. Foundation of MemKraft's entity structure.
**Absorbable ideas:** None remaining — fully absorbed.

### 6. Rowboat (rowboatlabs/rowboat) — 1k+ ⭐
**Link:** https://github.com/rowboatlabs/rowboat
**Core:** Persistent live-tracking with meeting prep. Desktop app.
**Strengths:**
- Live tracking with auto-update
- Meeting brief generation
- Calendar/email integration
**MemKraft learns:** Already adopted. Live Notes + Meeting Brief come from Rowboat.
**Absorbable ideas:** Calendar integration for meeting brief auto-generation.

---

## 💡 Improvement Ideas (by priority)

### Priority 1: Auto-Extract (from Mem0)
**What:** `memkraft extract "conversation text"` — auto-extract entities, facts, and relationships from any text.
**Why:** Currently manual `track` + `update`. Auto-extract removes friction.
**How:** regex entity detection (already built) + optional LLM for fact extraction → write to Markdown.
**Difficulty:** Medium | **Impact:** High | **Source:** Mem0

### Priority 2: Temporal Fact Tracking (from Zep)
**What:** When updating an entity, auto-detect if a fact changed and record the transition.
**Example:** Role changes from "CEO of StartupX" → "CEO of StartupY" → timeline gets "Role changed: StartupX → StartupY".
**Why:** Facts change. Knowing *when* they changed is as valuable as knowing *what* changed.
**Difficulty:** Medium | **Impact:** High | **Source:** Zep

### Priority 3: Cognify Pipeline (from Cognee)
**What:** `memkraft cognify` — process all inbox items into structured pages.
**Why:** Inbox captures raw items. Cognify turns them into properly classified entities/decisions/tasks.
**How:** Read inbox/*.md → RESOLVER classification → route to correct directory → generate page from template.
**Difficulty:** Low-Medium | **Impact:** Medium-High | **Source:** Cognee

### Priority 4: Hybrid Search (from Cognee/Zep)
**What:** `memkraft search "query"` — combine grep (exact) with optional semantic search (vector).
**Why:** Current `lookup` is exact-match only. Semantic search finds "hiring" when you search "recruitment".
**How:** Default grep (free, no deps). Optional `--semantic` flag with embedding model.
**Difficulty:** Medium | **Impact:** Medium | **Source:** Cognee, Zep

### Priority 5: Memory Tier Labels (from Letta)
**What:** Explicit tier labeling on each page: `Tier: core | recall | archival`.
**Why:** Makes the three-tier model explicit. Agents can prioritize core memory for context windows.
**How:** Add tier field to templates. `memkraft promote "Entity" --tier core` to upgrade.
**Difficulty:** Low | **Impact:** Medium | **Source:** Letta

### Priority 6: Cross-Entity Links
**What:** `[[wiki-links]]` between entity pages. Auto-suggest related entities.
**Why:** Knowledge compounds through connections, not just accumulation.
**How:** Track `[[links]]` in Markdown. `memkraft links "Entity"` shows graph.
**Difficulty:** Low-Medium | **Impact:** Medium | **Source:** Zettelkasten, Cognee

### Priority 7: Diff/Changelog
**What:** `memkraft diff` — show what changed since last run.
**Why:** Dream Cycle runs nightly. What did it actually do? What changed?
**How:** Git-style diff of memory directory.
**Difficulty:** Low | **Impact:** Medium | **Source:** General

---

## 🎯 Quick Wins (implement today)

| # | Idea | Effort | Impact |
|---|------|--------|--------|
| 1 | `memkraft extract` — pipe text → auto-detect entities | 2h | High |
| 2 | `memkraft cognify` — process inbox → structured pages | 3h | High |
| 3 | Memory tier labels in templates | 30min | Medium |
| 4 | `memkraft diff` — show changes since last Dream Cycle | 1h | Medium |
| 5 | `memkraft search` — improve lookup with fuzzy matching | 2h | Medium |
| 6 | `memkraft links "Entity"` — show backlinks | 1h | Medium |

---

## 🏗️ Major Features (roadmap)

| # | Feature | Timeline | Effort |
|---|---------|----------|--------|
| 1 | Auto fact extraction from conversations (regex + LLM) | v0.2 | 1 week |
| 2 | Temporal fact change tracking | v0.2 | 3 days |
| 3 | Optional semantic search (vector embeddings) | v0.3 | 1 week |
| 4 | Python/JS SDK for programmatic access | v0.3 | 2 weeks |
| 5 | Calendar/email integration for meeting briefs | v0.4 | 1 week |
| 6 | Multi-agent knowledge sharing protocol | v0.5 | 2 weeks |
| 7 | Web UI for browsing memory graph | v0.5 | 3 weeks |

---

## 📊 Competitive Positioning

```
Transparency ◀─────────────────────────────────────▶ Automation
   MemKraft    GBrain    Cognee    Letta    Mem0    Zep
   (Markdown)  (MD)      (Graph)   (Tiered) (API)   (API)

                 ◀─────────────────────────────────────▶
                 Offline/Git-friendly    Cloud-required
                 MemKraft, GBrain        Mem0, Zep, Letta
```

**MemKraft's sweet spot:** Maximum transparency + reasonable automation. You can read, edit, git-track everything. Auto-extraction adds convenience without sacrificing readability.
