# MemKraft v3 Paradigm Plan — Consolidating Memory

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Turn MemKraft from a local memory store/search package into a lifecycle-computed memory operating system for AI agents.

**Architecture:** v3 unifies Memory Gym, Hybrid Retrieval 2.0, Sleep Consolidation, Provenance, Outcome Learning, and Context Compilation into one paradigm: memory is continuously evaluated, consolidated, and optimized before retrieval. Runtime retrieval becomes the final stage of a memory lifecycle, not the core product.

**Tech Stack:** Python stdlib-first core, optional `memkraft[embedding]`, existing markdown/JSONL storage, SQLite graph, benchmark JSON gates, existing MemKraft mixins.

---

## 1. Core Thesis

MemKraft v3 is not a larger search engine. It is a shift in where intelligence lives.

v2 architecture is mostly **retrieval-time memory**:

```text
user/task asks → search current notes → rank snippets → inject top-k
```

v3 architecture becomes **lifecycle-computed memory**:

```text
raw events/notes
  → evaluated by Memory Gym
  → consolidated during sleep
  → typed as episodic / semantic / procedural
  → grounded by provenance
  → ranked by observed utility
  → compiled into task-specific context
  → injected with citations, warnings, and confidence
```

The paradigm shift is that memory stops being passive storage and becomes an adaptive substrate. Search is no longer the main event. Search is one operator inside a larger memory lifecycle.

## 2. Why This Is a New Paradigm

### 2.1 From “remembered text” to “computed memory”

Traditional agent memory systems store text chunks and retrieve similar chunks. Even when they add embeddings or graphs, the retrieval moment remains dominant.

MemKraft v3 changes the center of gravity:

- memory is improved while the agent is idle;
- memory records carry type, source, confidence, and utility;
- memory is evaluated against benchmarks before being trusted;
- memory becomes a compiled artifact optimized for a task and token budget.

This is analogous to a database moving from flat files plus grep to a full query planner, materialized views, indexes, statistics, and provenance.

### 2.2 From “top-k retrieval” to “context product compilation”

The API boundary changes.

v2 asks:

```python
mk.search(query, top_k=20)
```

v3 asks:

```python
mk.compile_context(task, budget=8000, objective="solve")
```

The output is not a list of hits. It is a context product:

- procedural lessons relevant to the task;
- current semantic facts;
- temporally relevant episodes;
- conflicts and stale warnings;
- provenance citations;
- compressed summaries at the right resolution;
- confidence/utility annotations.

That is a categorical interface upgrade.

### 2.3 From static recall to longitudinal learning

A normal retrieval system can only be evaluated at one moment. MemKraft v3 can be evaluated over time:

- Did this memory help agents complete tasks?
- Did a derived lesson cause regressions?
- Did a consolidation pass hallucinate facts?
- Did recall improve while latency stayed bounded?
- Did the system get better after repeated use?

This makes MemKraft closer to an adaptive operating layer than a library.

### 2.4 From unverifiable summaries to accountable memory

Sleep consolidation without provenance is dangerous. It creates confident false memories.

v3 makes provenance mandatory for derived memories:

```json
{
  "kind": "semantic",
  "value": "The system CLI imports MemKraft from /Users/gimseojun/memcraft/src/memkraft.",
  "derived_from": [
    {"file": "...", "span": [120, 210], "transform": "sleep.semantic_extract.v1"}
  ],
  "confidence": "high"
}
```

The agent can ask:

```python
mk.why(memory_id)
```

and inspect why the system believes something. This is the difference between “AI notes” and auditable agent memory.

### 2.5 From feature claims to benchmark-gated evolution

The recent pruning experiment proved the point: naive postings-first retrieval looked safe under an easy benchmark, then dropped to recall@20 min 0.25 on the adversarial `alpha zeta` query.

v3 therefore treats evaluation as part of the architecture, not a QA afterthought.

No major retrieval, consolidation, or learning change ships unless Memory Gym can show:

- quality improved or did not regress;
- latency stayed inside budget;
- derived memories remain grounded;
- task outcomes improve.

That is how v3 prevents self-delusion.

---

## 3. Functional Leaps Expected

### 3.1 Memory Gym: from ad-hoc benchmarks to continuous truth layer

**Current state:** `benchmarks/search_recall_bench.py`, search/cache/corpus/reasoning microbenches.

**v3 leap:** a unified benchmark harness:

```text
benchmarks/gym/
  scenarios.py
  metrics.py
  gates.py
  run.py
  fixtures/
  baselines/
```

Capabilities:

- adversarial search scenarios;
- temporal/stale fact scenarios;
- cross-note multi-hop scenarios;
- derived-memory hallucination checks;
- context compilation quality checks;
- regression gate JSON;
- candidate retriever plug-ins.

**Expected effect:** every performance feature becomes safer to ship. The biggest improvement is not raw speed; it is moving from intuition-driven development to measured memory engineering.

Success targets:

```text
- 100% of retrieval/consolidation PRs include a Memory Gym gate.
- Search candidate changes must preserve holdout recall@20 above threshold.
- Sleep consolidation must report hallucination/provenance sample checks.
```

### 3.2 Hybrid Retrieval 2.0: from lexical memory to semantic recovery

**Current state:** BM25/search_v2/search_smart are reliable; embedding/hybrid APIs exist but remain opt-in because older experiments were too slow and not clearly better.

**v3 leap:** hybrid retrieval becomes a benchmark-gated selectable engine, not an experimental side API.

Architecture:

```text
BM25 candidate set
+ dense semantic candidate set
+ graph/contextual expansions
+ RRF fusion
+ optional rerank
+ Gym quality/latency gates
```

Expected functional improvement:

- fewer failures on lexical mismatch;
- better cross-note recall;
- better broad task recall;
- less need for risky postings-only pruning;
- better Korean/English mixed query behavior if embeddings support it.

Expected performance improvement:

Hybrid may add compute, so the v3 performance expectation is not “all queries faster.” It is:

```text
quality-adjusted performance improves:
more correct recall per millisecond and fewer catastrophic misses.
```

Concrete gates:

```text
- adversarial recall@20 min: from 0.25 naive failure class to >= 0.80 for hybrid candidate on holdout
- BM25-only easy-query latency: no more than 15% regression when hybrid disabled
- hybrid p50 latency at 3k docs: <= 2x BM25 baseline initially, then optimized downward
- no category regression on LME/PersonaMem samples
```

### 3.3 Sleep Consolidation: from accumulation to digestion

**Current state:** `consolidation.py` has duplicate/stale/orphan/observation/contradiction stages. It is useful but not yet a memory lifecycle.

**v3 leap:** `mk.sleep()` becomes a first-class offline memory digestion API.

Pipeline:

```text
raw notes/session artifacts
→ episode index
→ semantic fact candidates
→ procedural lesson candidates
→ conflict/staleness review
→ provenance-linked derived records
→ updated context compiler views
```

Expected functional improvement:

- fewer noisy old facts in context;
- better multi-session recall;
- reusable procedural lessons extracted from repeated successes/failures;
- automatic conflict surfacing;
- lower token cost through materialized summaries.

Expected performance improvement:

Sleep moves work from query-time to idle-time.

```text
Instead of summarizing/filtering/compressing every query,
MemKraft precomputes derived views and uses them at query time.
```

Concrete gates:

```text
- compile_context p50 token assembly faster than raw top-k compression for large corpora
- context token count reduced 30-60% for equivalent answer quality on scenario tasks
- sleep dry-run reports exact planned writes
- derived fact hallucination sample rate < 2%
- every derived record has provenance or explicit provenance="unknown"
```

### 3.4 Provenance-first Memory: from confidence theater to auditable memory

**Current state:** confidence/prompt evidence modules exist, but provenance is not a universal contract.

**v3 leap:** derived memory is not accepted unless it carries source lineage.

Core model:

```python
MemoryRecord(
    id=str,
    kind="episodic|semantic|procedural",
    text=str,
    derived_from=[SourceSpan(...)],
    confidence=float|str,
    utility=float,
    valid_from=None,
    valid_to=None,
)
```

Expected functional improvement:

- agents can explain memory claims;
- stale or hallucinated derived facts can be traced;
- consolidation becomes safe enough to run automatically;
- user trust increases because memory is inspectable.

Expected performance improvement:

Not raw speed, but lower debugging cost and safer automation. Also enables context compiler to include short citations instead of bloated raw excerpts.

Concrete gates:

```text
- 100% new sleep-derived records include derived_from.
- mk.why(record_id) resolves to readable source context.
- provenance lookup p50 < 10ms for local stores up to 10k records.
```

### 3.5 Outcome-driven Learning Loop: from storing lessons to learning which lessons matter

**Current state:** ReasoningBank stores reusable task outcomes and supports injection with metadata.

**v3 leap:** every injected memory can receive outcome feedback.

API direction:

```python
block, meta = mk.reasoning_inject_for_task(task, return_metadata=True)
# agent works
mk.report_outcome(meta["usage_id"], success=True, evidence="tests passed")
```

The system learns:

- which lessons help;
- which memories are stale or harmful;
- which procedural patterns should be promoted;
- which should decay.

Expected functional improvement:

- better memory injection over time;
- fewer irrelevant procedural blocks;
- stronger agent performance on repeated project workflows;
- personalized memory utility per workspace/profile.

Expected performance improvement:

- fewer tokens wasted on low-utility memories;
- lower retrieval fanout because utility guides ranking;
- faster task startup because context compiler has better priors.

Concrete gates:

```text
- injected token budget reduced 20-40% with equal or better task success on replay scenarios
- high-utility memories appear more often in successful replay contexts
- failed/unused memories decay unless manually pinned
```

### 3.6 Context Compiler: from search API to task substrate

**Current state:** `context_compress.py` selects and compresses search hits. It is deterministic and useful, but it compresses rows rather than compiling a memory product.

**v3 leap:** `compile_context` orchestrates memory kind, utility, provenance, temporal relevance, and token budget.

Target API:

```python
compiled = mk.compile_context(
    task="debug gateway Telegram profile routing",
    budget=8000,
    objective="execute",
    include_provenance=True,
)
```

Potential output sections:

```text
## Procedural Lessons
## Current Stable Facts
## Relevant Episodes
## Open Risks / Conflicts
## Source Citations
```

Expected functional improvement:

- better LLM task performance than raw top-k;
- fewer missing “how-to” memories;
- less stale fact injection;
- cleaner task-specific prompts.

Expected performance improvement:

- token efficiency improves even if retrieval cost is similar;
- context assembly becomes deterministic and cacheable;
- expensive summarization shifts to sleep-time.

Concrete gates:

```text
- compiled context solves more replay tasks than top-k context under same token budget
- compiled context uses <= 70% tokens of raw top-k for same answer quality
- p50 compile latency <= 100ms at 3k docs when using precomputed sleep views
```

---

## 4. Unified v3 Architecture

```text
                           ┌────────────────────┐
                           │     Memory Gym     │
                           │ quality/latency QA │
                           └─────────┬──────────┘
                                     │ gates every change
                                     ▼
┌──────────────┐     ┌─────────────────────────────┐
│ Raw Inputs   │────▶│ Sleep Consolidation Pipeline │
│ notes/events │     │ dedupe, episodes, facts,    │
│ sessions     │     │ procedures, conflicts       │
└──────┬───────┘     └──────────────┬──────────────┘
       │                            │ derived records
       │                            ▼
       │                  ┌──────────────────────┐
       │                  │ Provenance Store     │
       │                  │ why/derived_from     │
       │                  └──────────┬───────────┘
       │                             │
       ▼                             ▼
┌─────────────────┐        ┌──────────────────────┐
│ Hybrid Retrieval│◀──────▶│ Typed Memory Records │
│ BM25+dense+RRF  │        │ episodic/semantic/   │
└────────┬────────┘        │ procedural           │
         │                 └──────────┬───────────┘
         ▼                            │ utility feedback
┌────────────────────┐                ▼
│ Context Compiler   │◀──────┌──────────────────────┐
│ task+budget output │       │ Outcome Learning     │
└─────────┬──────────┘       │ usage_id/success     │
          │                  └──────────────────────┘
          ▼
   Agent-ready context
```

## 5. Why All Pieces Belong in v3 Together

Individually, each piece can look incremental:

- benchmark harness;
- hybrid retrieval;
- consolidation;
- provenance;
- outcome scoring;
- context compression.

Together they form a new operating model.

| Piece | Alone | In v3 System |
|---|---|---|
| Memory Gym | benchmark scripts | truth layer controlling all memory evolution |
| Hybrid Retrieval | better search mode | semantic recovery engine validated by Gym |
| Sleep | cleanup job | background memory digestion and materialized views |
| Provenance | citation metadata | safety condition for automatic memory generation |
| Outcome Loop | analytics | self-improving utility signal |
| Context Compiler | formatting helper | primary API for agent memory consumption |

The paradigm is the feedback loop:

```text
Use memory → observe outcome → update utility → sleep/consolidate → compile better context → measure with Gym → repeat
```

That loop does not exist in v2.

---

## 6. Expected v3-Level Improvements

These are ambitious but testable targets, not guaranteed marketing claims.

### Search/retrieval quality

```text
- adversarial recall@20 min: 0.25 naive failure class → >= 0.80 holdout
- easy-query recall: no regression from v2.12 baseline
- cross-note/multi-hop scenario success: +20-40% relative over BM25-only baseline
```

### Context efficiency

```text
- compiled context token usage: 30-60% lower than raw top-k for equivalent task answer quality
- procedural memory injection precision: fewer irrelevant lessons under fixed budget
```

### Query-time latency

```text
- BM25-only path: no regression when hybrid disabled
- write→first-search path: preserve v2.12 incremental index gains
- compile_context p50 <= 100ms at 3k docs using precomputed sleep views
- repeated query: preserve sub-ms cache hits
```

### Memory health

```text
- derived record provenance coverage: >= 95% initially, target 100% for sleep-generated records
- sampled derived fact hallucination rate: < 2%
- stale/conflict surfacing: measurable detection count, no silent overwrite
```

### Agent outcome

```text
- replay task success: +15-30% relative over raw search injection on project workflows
- injected token waste: -20-40% by utility-guided selection
- repeated workflow setup time: lower due to procedural memory promotion
```

## 7. v3 Milestone Plan

> **Vooy-informed roadmap update:** The original v3 milestones below are the conceptual spine. After reviewing the vooy Memory master spec, the implementation roadmap was expanded in `docs/plans/2026-07-08-memkraft-v3-vooy-informed-roadmap.md` to add product-neutral lifecycle primitives: candidate memory, resolver dry-run, session read-your-writes overlay, generic last-interaction index, compiled truth/timeline views, local governance, and tool/API procedural memory lite. A follow-up Fable5 research pass refined the release train further in `docs/plans/2026-07-08-memkraft-v3-fable5-refined-roadmap.md`, especially by adding a storage/governance foundation, `extract_claims`, CI-gated Gym metrics, and a new `2.15.0` cut between context compilation and true `3.0.0`.

### Milestone 0 — Baseline Freeze

**Goal:** Establish v2.12 as the baseline.

Files:

```text
benchmarks/gym/
docs/bench/V3_BASELINE.md
```

Tasks:

1. Move current search recall bench into Gym format.
2. Add scenario registry.
3. Add gate JSON.
4. Capture baseline for search, ReasoningBank, corpus index, context compression.
5. Save report.

Exit criteria:

```text
python benchmarks/gym/run.py --gate exits 0 on v2.12 baseline.
```

### Milestone 1 — Hybrid Retrieval 2.0

**Goal:** Improve semantic recovery without breaking BM25 baseline.

Files:

```text
src/memkraft/embedding.py
src/memkraft/search.py
src/memkraft/rrf.py
benchmarks/gym/scenarios_search.py
```

Tasks:

1. Add Gym candidate hooks for `search_hybrid`.
2. Run alpha/k sweeps.
3. Optimize index loading/caching.
4. Add no-extra fallback tests.
5. Gate recall/latency.

Exit criteria:

```text
Hybrid improves adversarial holdout recall without BM25 regression.
```

### Milestone 2 — Provenance Core

**Goal:** Introduce source spans and `mk.why()` without changing existing storage too much.

Files:

```text
src/memkraft/provenance.py
src/memkraft/__init__.py
tests/test_provenance.py
```

Tasks:

1. Define `SourceSpan` and `DerivedRecord` helpers.
2. Add provenance sidecar JSONL under `.memkraft/provenance.jsonl`.
3. Add `mk.record_provenance(...)`.
4. Add `mk.why(record_id)`.
5. Allow `provenance="unknown"` for legacy.

Exit criteria:

```text
New derived records can be traced to source spans in <10ms local lookup.
```

### Milestone 3 — Sleep Consolidation Lite

**Goal:** Turn consolidation into safe, auditable memory digestion.

Files:

```text
src/memkraft/consolidation.py
src/memkraft/cli.py
tests/test_sleep.py
```

Tasks:

1. Add `mk.sleep(dry_run=True)` as wrapper over consolidation stages.
2. Add sleep plan/diff output.
3. Add episode index stage.
4. Add semantic candidate stage with provenance.
5. Add CLI `memkraft sleep`.

Exit criteria:

```text
memkraft sleep --dry-run shows planned derived writes with source spans.
```

### Milestone 4 — Outcome Learning

**Goal:** Close the loop between injected memories and task results.

Files:

```text
src/memkraft/reasoning_bank.py
src/memkraft/utility.py
tests/test_memory_utility.py
```

Tasks:

1. Add usage IDs to injection metadata.
2. Add `mk.report_outcome(...)`.
3. Store utility events JSONL.
4. Add conservative utility scoring.
5. Incorporate utility into injection ranking.

Exit criteria:

```text
Successful replay memories are promoted; failed/unused memories downranked.
```

### Milestone 5 — Context Compiler Alpha

**Goal:** Make `compile_context` the primary agent-facing memory API.

Files:

```text
src/memkraft/context_compile.py
src/memkraft/context_compress.py
tests/test_context_compile.py
```

Tasks:

1. Add `compile_context(task, budget, objective)`.
2. Mix episodic/semantic/procedural records.
3. Include provenance citations.
4. Add utility/token greedy selection.
5. Benchmark against raw top-k injection.

Exit criteria:

```text
Compiled context beats raw top-k under same token budget in Gym replay tasks.
```

---

## 8. Positioning

v2 positioning:

> MemKraft is a local-first compound memory system for AI agents.

v3 positioning:

> MemKraft is a local-first memory operating system for AI agents: it evaluates, consolidates, grounds, learns from, and compiles memory into task-ready context.

Shorter:

> MemKraft v3 turns memory from passive recall into an adaptive lifecycle.

## 9. Non-goals

Avoid these traps in v3:

- Do not make hybrid embeddings mandatory.
- Do not ship sleep-generated facts without provenance.
- Do not optimize latency by accepting recall collapse.
- Do not build CRDT/multi-device sync before the core lifecycle is proven.
- Do not claim self-improvement until outcome feedback changes future retrieval behavior.
- Do not let LLM summarization overwrite raw memories.

## 10. First Concrete PR

Start with Memory Gym, because every later claim depends on it.

```text
PR: feat: add memory gym benchmark harness
```

Minimum scope:

```text
benchmarks/gym/__init__.py
benchmarks/gym/metrics.py
benchmarks/gym/scenarios.py
benchmarks/gym/run.py
benchmarks/gym/gates.py
tests/test_memory_gym.py
docs/bench/V3_BASELINE.md
```

This PR does not change production behavior. It creates the truth layer required for v3.

---

## 11. The v3 Promise

A v3-worthy system should make three claims and prove them:

1. **It remembers better.**
   - higher adversarial/multi-hop recall;
   - fewer stale/conflicting facts;
   - better procedural reuse.

2. **It thinks cheaper.**
   - less token waste;
   - more work shifted to idle-time;
   - context assembled from precomputed memory products.

3. **It improves with use.**
   - outcome feedback changes future ranking;
   - sleep creates better derived records;
   - Memory Gym catches regressions.

If v3 does not prove all three, it is not v3. It is just v2.13 with extra features.
