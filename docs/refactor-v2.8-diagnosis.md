# MemKraft v2.8 Refactor Diagnosis

Date: 2026-05-02  
Branch: `refactor/v2.8-comprehensive`  
Baseline commit: `6821a91 chore: v2.7.4 — embedding strictly opt-in, BM25 remains default`

## 0. Baseline Snapshot

| Metric | Value |
|---|---|
| Installed version | 2.7.4 |
| Source modules | 47 |
| Source LOC total | 21,442 |
| Test files | 55 |
| Test result | 1254 passed, 3 skipped, 36 warnings in 34-36s |
| Coverage (branch, source=src/memkraft) | 70% statements, missing 2,993 statements |
| Coverage partial branches | 749 / 4,914 |
| Untracked write 100x | ~10-11ms total, p50 0.09ms, p95 0.12ms |
| Tracked write 100x | ~15ms total, p50 0.13ms, p95 0.24ms |
| Search 100x (tracked store, query='info') | ~1,226-1,646ms total, p50 12-16ms, p95 13-19ms |
| cProfile profile (500 updates + 100 searches) | 5.28s total |
| Radon modules scanned | 44 files, 582 functions |
| Internal import edges | 49 |
| Internal cycles | 0 detected |

Takeaway: write path is cheap; search path dominates runtime and is I/O-heavy.

## 1. Module Responsibility Map

All modules are in `src/memkraft/`.

### 1.1 Core public API shell

`__init__.py` (237 LOC) dynamically creates the final `MemKraft` class by composing:

- `_BaseMemKraft` from `core.py`
- 24 mixins from feature modules

Public inheritance order (read top → bottom):

1. CoreMemKraft (core.py)
2. BitemporalMixin
3. DecayMixin
4. LinksMixin
5. TiersMixin
6. IncidentMixin
7. RunbookMixin
8. RCAMixin
9. DecisionStoreMixin
10. PromptTuneMixin
11. PromptEvidenceMixin
12. ConvergenceMixin
13. SearchMixin
14. ChunkingMixin
15. LifecycleMixin
16. GraphMixin
17. MultimodalMixin
18. MultiPassMixin
19. RoutingMixin
20. RRFMixin
21. ConsolidationMixin
22. TemporalChainMixin
23. ConfidenceMixin + ImplicitAcquireMixin
24. ContextCompressMixin
25. RerankMixin
26. HierarchicalMixin
27. AliasMixin
28. CacheSearchMixin + CacheWriteMixin
29. ReasoningBankMixin
30. EmbeddingMixin
31. PreferenceMixin (conditionally attached if supported)

Observation: mixin architecture already exists, but the class name exposed to users is stable. Good: public API surface is not tied to internal decomposition.

### 1.2 Large modules

| Module | LOC | Public symbols | Import count | Primary responsibility |
|---|---:|---:|---:|---|
| `core.py` | 4,724 | 68 | 16 | Base MemKraft class: filesystem model, init, mutation, detection, retrieval primitives, health, retro, dream, agent inject, snapshot/time-travel, bulk helpers |
| `personamem.py` | 933 | 10 | 4 | PersonaMem context extraction + persona-grounded memory |
| `lifecycle.py` | 857 | 9 | 18 | Autonomous memory lifecycle: summarization, decay sweeps, maintenance |
| `graph.py` | 826 | 10 | 8 | SQLite-backed knowledge graph CRUD + BFS + extraction |
| `consolidation.py` | 744 | 2 | 7 | Sleep consolidation, contradiction detection |
| `cli.py` | 717 | 1 | 22 | CLI entrypoint only |
| `search.py` | 703 | 8 | 6 | Smart/exact/semantic retrieval layer |
| `multi_pass.py` | 638 | 2 | 5 | Multi-pass retrieval pipeline |
| `routing.py` | 593 | 2 | 6 | Question-type routing + counting retrieval |
| `reasoning_bank.py` | 583 | 7 | 9 | Reasoning trajectories/episodes |
| `embedding.py` | 573 | 9 | 9 | Optional local embedding retrieval |
| `prompt_tune.py` | 564 | 3 | 7 | Prompt registration/eval |
| `decay.py` | 536 | 8 | 8 | Frontmatter parsing, decay math, tier helpers |
| `confidence.py` | 464 | 5 | 4 | Confidence scoring + implicit acquisition |
| `decision_store.py` | 458 | 11 | 11 | Decision log CRUD |
| `multimodal.py` | 423 | 5 | 6 | Attachments + multimodal search |
| `bitemporal.py` | 412 | 9 | 5 | Fact timeline CRUD |
| `temporal_chain.py` | 374 | 1 | 4 | Multi-session temporal chains |
| `incident.py` | 379 | 5 | 5 | Incident recording/search/update |
| `convergence.py` | 381 | 2 | 7 | Convergence analysis |
| `preference.py` | 347 | 7 | 5 | Preference tracking |
| `cache.py` | 316 | 11 | 7 | TTL + size-bounded result cache |
| `rrf.py` | 307 | 3 | 2 | Reciprocal rank fusion utility |
| `preference_graph_sync.py` | 311 | 4 | 4 | Preference → graph sync |
| `hierarchical.py` | 276 | 3 | 5 | Multi-level memory retrieval |
| `prompt_evidence.py` | 272 | 2 | 6 | Prompt evidence CRUD |
| `rerank.py` | 263 | 2 | 4 | Lightweight re-ranking |
| `links.py` | 264 | 6 | 6 | Wiki-link CRUD |
| `runbook.py` | 256 | 4 | 7 | Runbook match/apply |
| `incident_storage.py` | 242 | 18 | 7 | Shared doc/JSON storage primitives |
| `tiers.py` | 235 | 8 | 5 | Tier read/update |
| `stats.py` | 245 | 6 | 11 | Doctor/stats helpers |
| `chunking.py` | 226 | 4 | 2 | Precision chunk search |
| `templates_pkg/__init__.py` | 104 | 5 | 6 | Template defaults |
| `mcp_admin.py` | 199 | 3 | 10 | MCP admin |
| `rca.py` | 197 | 2 | 5 | Root-cause analysis |
| `context_compress.py` | 231 | 2 | 3 | Context compression |
| `alias.py` | 171 | 8 | 5 | Entity aliasing |
| `mcp.py` | 167 | 2 | 13 | MCP server |
| `watch.py` | 132 | 6 | 10 | FS watcher |
| `selfupdate.py` | 111 | 6 | 10 | Self-update helper |
| `agents_hint.py` | 121 | 4 | 6 | Agent hint injection |
| `doctor.py` | 340 | 5 | 10 | Doctor diagnostics |

### 1.3 `core.py` responsibility decomposition (inside single class)

`core.py` exposes one class `MemKraft` with 114 methods.

Grouping by observed responsibility:

1. **Init / config / directory model**  
   `__init__`, `init`

2. **Entity mutation**  
   `track`, `update`, `list_entities`

3. **Brief / structured retrieval for meetings**  
   `brief`

4. **Detection pipelines**  
   `detect`, `detect_conflicts`, `_is_opposing`, `_extract_bullet_facts`

5. **Lifecycle / summarization**  
   `dream`, `_compression_suggestion`

6. **Search internals**  
   `search`, `_search_tokens`, `_bm25_score`, `_all_md_files`, `_safe_read`, `_touch_last_accessed`, `_detect_regex`

7. **Agentic search / temporal**  
   `agentic_search`, `time_travel`, `snapshot_entity`, `snapshot_diff`

8. **Health / retro / diagnostics**  
   `health_check`, `retro`

9. **Injection / automation**  
   `agent_inject`

10. **Bulk helpers / migration**  
    large utility functions for batch transforms

This is the strongest decomposition candidate.

## 2. Performance Hotspots

### 2.1 cProfile summary (top cumulative contributors)

File: `docs/bench/v28_baseline_profile.txt`

| Rank | Function | cumtime | calls | Observation |
|---|---|---:|---:|---|
| 1 | `core.py:1286(search)` | 5.206s | 100 | Search is the entrypoint bottleneck |
| 2 | `core.py:4094(_search_tokens)` | 2.902s | 30,100 | Per-candidate token scanning |
| 3 | `re.findall` (stdlib) | 1.854s | 40,100 | Regex matching dominates |
| 4 | `{method 'findall' of 're.Pattern' objects}` | 1.830s | 40,100 | Same underlying |
| 5 | `core.py:4096(<listcomp>)` | 0.860s | 30,100 | Lowercasing / normalization |
| 6 | `{method 'lower'}` | 0.449s | 70,948 | String normalization |
| 7 | `pathlib.read_text` | 0.411s | 21,120 | Disk read for candidate files |
| 8 | `dict.get` | 0.352s | 9,171,350 | High-frequency dict access |
| 9 | `posix.open` | 0.221s | 22,240 | Low-level file open |
| 10 | `core.py:4024(_safe_read)` | 0.202s | 10,000 | Safe file reader |
| 11 | `strptime` chain | 0.066s | 10,000 | Date parsing per update |
| 12 | `_all_md_files` | 0.061s | 10,100 | File enumeration |
| 13 | `_touch_last_accessed` | 0.059s | 620 | Access timestamp touch |

### 2.2 Bottleneck classification

| Class | Dominant root cause |
|---|---|
| CPU | Regex re-evaluation per query candidate, repeated lowering, string ops |
| I/O | Candidate file read on every search (`read_text`), glob enumeration |
| Memory/alloc | Frequent pathlib object creation, per-query temporary lists |

### 2.3 Observations from grep / source inspection

1. **`re.findall(...)` is called inline in hot functions.**  
   Some patterns are complex org/entity regex and appear constructed at call-time.  
   This means compile cost is paid repeatedly.

2. **Candidate enumeration appears to rescan markdown directories repeatedly.**  
   `_all_md_files` + multiple directory globs in `core.py` are called inside search and related workflows.

3. **`_safe_read` is called thousands of times with fresh file opens.**  
   No apparent in-memory caching layer above filesystem for recently-read note content.

4. **`strptime` is invoked for date/timestamp handling on updates.**  
   10,000 calls for 500 updates suggests repeated parsing inside append/write workflows.

## 3. Code Smell Scan

### 3.1 Function length > 80 lines (selected high-risk)

| Function | Lines | Risk |
|---|---:|---|
| `core.py:1286 search` | ~69 complexity | too long + high complexity |
| `core.py:429 dream` | 429-? | mixed lifecycle + summarization |
| `core.py:2503 agentic_search` | 122 | mixed control flow |
| `core.py:1736 health_check` | 60 complexity | diagnostic logic should be isolated |
| `core.py:3803 _detect_regex` | 141 | regex compilation + matching |
| `core.py:4500 time_travel` | 138 | temporal logic should be isolated |
| `core.py:291 brief` | 122 | structured assembly |
| `core.py:781 resolve_conflicts` | 89 | conflict logic |
| `personamem.py:630 build_context` | 95 complexity | high complexity |
| `cli.py:10 main` | 129 complexity | acceptable for CLI, but still large |
| `multi_pass.py:506 search_multi` | 133 | pipeline orchestration |
| `routing.py:333 _search_counting` | 118 | retrieval+counting |
| `rrf.py:95 rrf_fuse` | 117 | fusion utility |
| `consolidation.py:476 _consolidate_contradictions` | 101 | maintenance logic |

### 3.2 Cyclomatic complexity (radon)

Top files by average complexity (minimum 5 functions):

| File | Avg CC | Max CC | Functions |
|---|---:|---:|---:|
| `prompt_tune.py` | 19.71 | 42 | 7 |
| `convergence.py` | 19.50 | 65 | 8 |
| `runbook.py` | 15.17 | 39 | 6 |
| `temporal_chain.py` | 14.80 | 34 | 5 |
| `incident.py` | 14.33 | 42 | 9 |
| `multi_pass.py` | 12.80 | 29 | 10 |
| `routing.py` | 12.73 | 23 | 11 |
| `personamem.py` | 11.95 | 95 | 20 |
| `prompt_evidence.py` | 11.88 | 35 | 8 |
| `hierarchical.py` | 11.67 | 23 | 6 |
| `search.py` | 10.87 | 30 | 15 |
| `core.py` | 10.59 | 69 | 115 |

Top individual complexity hotspots:

| Function | CC | Rank |
|---|---:|---|
| `cli.main` | 129 | F |
| `personamem.build_context` | 95 | F |
| `core.search` | 69 | F |
| `convergence.ConvergenceMixin` | 65 | F |
| `convergence.convergence_check` | 64 | F |
| `core.health_check` | 60 | F |
| `core._detect_regex` | 60 | F |
| `core.dream` | 51 | F |
| `core.agentic_search` | 45 | F |

### 3.3 Logging hygiene

| Metric | Value |
|---|---|
| `print(...)` calls in src | 336 |
| `logging` or `logger` mentions in src | 7 |

Top files with raw `print`:

| File | Count |
|---|---:|
| `core.py` | 201 |
| `cli.py` | 46 |
| `doctor.py` | 35 |
| `mcp_admin.py` | 20 |
| `watch.py` | 12 |
| `selfupdate.py` | 8 |
| `agents_hint.py` | 5 |
| `stats.py` | 4 |
| `decay.py` | 4 |
| `mcp.py` | 1 |

Conclusion: logging is not standardized; debug/status output relies on `print`.

### 3.4 Type hints

| Metric | Value |
|---|---|
| Functions scanned | 581 |
| Functions with all args annotated (excluding self/cls) | 470 |
| Functions with return annotation | 546 |

Coverage is already decent, but core/search/routing hot methods need verification.

### 3.5 Dead code / unused code

`vulture` found no high-confidence unused code items on the current source tree.

This does not mean no dead code exists; it means the static detector did not flag confident candidates. Manual review still recommended in CLI/docs helpers.

### 3.6 Internal import graph

- Nodes: 47
- Edges: 49
- Cycles detected: 0

Import structure is currently clean.

## 4. Debuggability

### 4.1 Current issues

1. **Poor observability** because most runtime output is `print` rather than structured logging.
2. **Error context quality varies.**  
   Hot paths raise/return generic warnings without enough breadcrumb context.
3. **Search path is hard to trace** because a single `search()` call fans out into tokenization, candidate enumeration, file reads, regex passes, and scoring in a single large method chain.

### 4.2 Practical improvement targets

- Add logger per module.
- Add breadcrumbs for:
  - query normalization
  - candidate set size
  - matched file count
  - top-K final scores
- Avoid swallowing exceptions silently in search helpers.

## 5. Test Coverage Notes

Coverage JSON: `docs/bench/coverage.json`

| Metric | Value |
|---|---|
| Covered lines | 7,913 |
| Total statements | 10,906 |
| Coverage | ~70% |
| Missing lines | 2,993 |
| Branches | 4,914 |
| Missing branches | 1,791 |

Recommendation: before changing search internals, expand regression tests for:
- exact query
- fuzzy query
- alias-resolved query
- multi-word query
- empty-result query
- update-then-search consistency

## 6. Top Findings (Measurement-based)

1. **Search path is CPU + I/O bound.**  
   Regex matching + candidate file reads dominate runtime.

2. **`core.py` is the primary complexity hub.**  
   4,724 LOC, 115 methods, top CC functions all live here.

3. **Regex patterns appear recomputed in hot paths.**  
   Especially org/entity detection patterns.

4. **Candidate file read path has no effective upper-layer cache for repeated reads.**

5. **Logging/print hygiene is weak.**  
   336 raw prints vs almost no standardized logger usage.

6. **Public API can be preserved while decomposing internals.**  
   Mixin composition is already in place.

## 7. Recommended Priority

### P0 (High ROI, low risk)
- Extract `core.py` internal helpers into private mixins/internal modules without changing public API
- Precompile hot regexes once
- Add bounded cache for recently-read note content
- Add logger instrumentation to search/update hot path

### P1 (Good ROI, medium risk)
- Reduce complexity in `search`, `_search_tokens`, `_detect_regex`
- Add targeted regression tests before deeper retrieval refactor
- Replace `print` with logger in library code (keep CLI output clean)

### P2 (Lower ROI or higher risk)
- Reduce complexity in `personamem`, `convergence`, `prompt_tune`
- Evaluate candidate enumeration strategy for search
- Improve exception context messages
