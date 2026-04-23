# CHANGELOG

## [1.1.0] — 2026-04-23

### Added

- **`flush(source_path, strategy='auto')`** — Import external markdown file → MemKraft structured data. Supports `auto` (section-aware), `events` (list items), and `facts` (table rows) strategies.
- **`compact(max_chars=15000, dry_run=False)`** — Automatically move old/low-importance entities to archival tier. Supports `dry_run` preview mode.
- **`digest(output_path, max_chars=15000)`** — Render MemKraft state → MEMORY.md, always ≤ `max_chars`. Guaranteed no overflow.
- **`health()`** — Diagnose memory health: total size, tier distribution, entity count, recommendations, and status (`healthy` / `warning` / `critical`).
- **`LifecycleMixin`** (`src/memkraft/lifecycle.py`) — additive mixin; no changes to existing APIs.
- **Tests:** `tests/test_v110_lifecycle.py` (20 cases) covering flush/compact/digest/health with edge cases.

### Motivation

MEMORY.md grew to 153KB due to add-only pattern + broken nightly cleanup. MemKraft 1.1.0 makes this impossible: memory manages itself.

### Total APIs: 16 (12 existing + 4 new lifecycle APIs)

```bash
pip install --upgrade memkraft
```

All v1.0.3 APIs remain unchanged. Existing call sites are unaffected.

---

## [1.0.3] — 2026-04-23

### Added

- **`track_document(doc_id, content, chunk_size=500, chunk_overlap=50, entity_type="document", source="")`** — auto-chunking for long documents. Splits content into ~chunk_size-word overlapping chunks (BM25-style) and tracks each as `{doc_id}__c{idx}`. A parent entity is also tracked so callers can group chunk hits back to the source.
- **`search_precise(query, top_k=5, score_threshold=0.1)`** — precision-first search. Runs `search(fuzzy=False)` with a score threshold; falls back to `search(fuzzy=True)` with a relaxed threshold (`score_threshold * 0.5`) if the precision pass returns nothing.
- **`ChunkingMixin`** (`src/memkraft/chunking.py`) — additive mixin wired via `__init__.py`; no changes to `core.py` or existing signatures.
- **Tests:** `tests/test_v103_chunking.py` (13 cases) covering chunk math, overlap, parameter validation, search round-trip, threshold filtering, and fuzzy fallback.

### Performance (AMB PersonaMem pilot, 2026-04 Zeon)

- **PersonaMem 32k:** MemKraft **80%** vs BM25 70% (**+10pp**)
- **PersonaMem 128k:** MemKraft **75%** vs BM25 50% (**+25pp**)

### Upgrade

```bash
pip install --upgrade memkraft
```

All v1.0.2 APIs remain unchanged. Existing call sites are unaffected.

---

## [1.0.2] — 2026-04-22

### Added

- **LongMemEval benchmark harness** (`benchmarks/longmemeval/`):
  - `run.py` — oracle/s subset evaluation
  - `llm_judge.py` — LLM-as-judge scoring
  - `run_majority_vote.py` — 3-run semantic majority vote
  - Achieves **98.0%** on LongMemEval (LLM-judge, oracle 50, 3-run majority)
  - Surpasses prior SOTA: MemPalace (96.6%), MEMENTO/MS (90.8%)
- **SearchMixin APIs** (`src/memkraft/search.py`):
  - `search_v2`, `search_expand`, `search_temporal`, `search_ranked`, `search_smart`
- **`[bench]` optional dependency group** for benchmark reproduction (`pip install memkraft[bench]`)
- **README:** Added "Reproducing LongMemEval Results" section with full reproduction guide
- **Tests:** New `tests/test_v102_search.py` covering SearchMixin surface

### Fixed

- Benchmark harness: temporal questions excluded from aggregation detection (v3 fix)
- Benchmark harness: assistant content truncation at 1500 chars (v4 fix)
- Benchmark harness: full content sidecar for long assistant responses (v4.4 fix)

### Upgrade

```bash
pip install --upgrade memkraft
# or with benchmark extras:
pip install --upgrade 'memkraft[bench]'
```

No breaking changes. All new APIs are additive.

---

## [1.0.1] — 2026-04-21

### Polish & Bug Fixes

First patch after 1.0.0. Zero breaking changes; all fixes are additive
or opt-in.

- **README:** Added the hero "1.0 Self-Improvement Loop" quickstart
  block covering `prompt_register` → `prompt_eval` → `prompt_evidence`
  → `convergence_check`. Verified end-to-end.
- **`prompt_register`:** Now emits a `UserWarning` when `path` points to
  a non-existent file (silent typo fix). Pass `validate_path=True` to
  promote the warning to `FileNotFoundError` for stricter pipelines.
- **`prompt_eval`:** Rejects calls with *both* empty `scenarios` and
  empty `results` (`ValueError`) — recording an empty iteration polluted
  the ledger without analytical value.
- **`prompt_eval`:** Emits a `UserWarning` when a result references a
  scenario name not declared in `scenarios`, catching copy-paste drift
  that previously persisted as bad data.
- **`convergence_check`:** The `insufficient-iters` response now surfaces
  the iteration numbers that *were* found (previously always `[]`).
  When no iterations exist, `suggested_next` is `"first-iteration"`
  instead of the misleading `"patch-and-iterate"`.
- **Tests:** +10 (total 741 passing, 1 skipped).

### Upgrade

```bash
pip install --upgrade memkraft
```

## [1.0.0] — 2026-04-21

### Philosophy

> Bitemporal memory × empirical tuning: the first self-improvement ledger for AI agents.

Your agent's accountable past, in plain Markdown.

### New in 1.0.0

- **`prompt_register`** — Register any prompt/skill as a tracked entity with tier + metadata.
- **`prompt_eval`** — Record one empirical tuning iteration (scenarios + results) as a bitemporal decision + incident (on regression).
- **`prompt_evidence`** — Cite past tuning results via bitemporal decision search. As-of-then view, not a rewrite.
- **`convergence_check`** — Auto-judge mizchi-style convergence with decay-weighted pass-rate trend.

Together these four APIs close the loop that began in 0.5: **register → tune → recall → decide**, every step auditable and time-travelable in plain Markdown.

### Upgrade from 0.9.x

**Zero breaking changes.** All 0.9.x public APIs keep their exact signatures. Storage layout unchanged. See [MIGRATION.md](./MIGRATION.md).

```bash
pip install --upgrade memkraft
```

### Architecture

1.0.0 is an **integration release**, not a new-backend release.

- ✅ No new storage engines. Pure Markdown + frontmatter, as always.
- ✅ No new required dependencies. Core stays zero-dep.
- ✅ No LLM calls inside MemKraft. The ledger is data; the agent is the LLM.
- ✅ Every 0.9.x primitive now composes into the full self-improvement loop.

### API surface (total: 12 public methods)

| API | Since | Role |
|-----|-------|------|
| `track` | 0.5 | Start tracking an entity |
| `update` | 0.5 | Append information to an entity |
| `search` | 0.5 | Hybrid search (exact + IDF + fuzzy) |
| `tier_set` | 0.8 | Set tier: `core` / `recall` / `archival` |
| `fact_add` | 0.8 | Record a bitemporal fact |
| `log_event` | 0.8 | Log a timestamped event |
| `decision_record` | 0.9 | Capture a decision with rationale |
| `evidence_first` | 0.9 | Retrieve evidence before acting |
| `prompt_register` | **1.0** | Register a prompt/skill as an entity |
| `prompt_eval` | **1.0** | Record one tuning iteration |
| `prompt_evidence` | **1.0** | Cite past tuning results |
| `convergence_check` | **1.0** | Auto-judge convergence |

### Tests

731 passed, 1 skipped (same as 0.9.2a2 — 1.0.0 adds zero new test code; it's a stabilization + docs release).

### Deprecations

None.

---

## [0.8.4] - 2026-04-18

### Fixed
- `brief()` now returns text from MCP recall path (critical bug: existing entities reported as `found: False`) (#3)
- `track()` correctly returns `Path` matching `Optional[Path]` signature (#2)

### Improved
- Korean josa (조사) stripping with longest-match for complex particles (`에서`, `한테서`, `로서`) + 2-char guard for names like "이은" (#1)

### Tests
- 594 passed, 1 skipped (+21 from 0.8.3)

---

## [0.8.3] - 2026-04-17

### Added
- **`memkraft init --template <name>`** — 5 presets: `claude-code`, `cursor`, `mcp`, `minimal`, `rag`. Idempotent: existing files are preserved on re-run.
- **`memkraft templates list`** — browse available scaffolding templates.
- **`memkraft agents-hint --format json`** — structured output for CI/automation. All 6 targets emit a stable envelope with `{target, version, base_dir, content}`.
- **`memkraft doctor --fix [--dry-run] [--yes]`** — auto-repair missing `memory/` structure. **Create-only, never deletes.** Prompts for confirmation unless `--yes` or `--dry-run`.
- **`memkraft stats`** — workspace dashboard. `--export json|csv` + `--out <path>` for CI.
- **`memkraft mcp doctor`** / **`memkraft mcp test`** — production validation for the MCP server. `doctor` checks extras + entry point + tool schemas + Claude Desktop config location. `test` runs a remember→search→recall round-trip in a temp workspace.
- **`docs/mcp-setup.md`** — Claude Desktop + Cursor integration guide, including troubleshooting.

### Changed
- `pyproject.toml` gains richer `project.urls` (Documentation, Bug Tracker, Changelog).
- New package data: `templates_pkg/*.json` shipped in the wheel.

### Compatibility
- Fully backward-compatible with 0.8.2. All 538 existing tests still pass; 35 new tests added (573 total).

---

## [0.8.2] - 2026-04-17

### Added
- **`memkraft selfupdate`** — self-upgrade via pip when a newer release is on PyPI. `--dry-run` to check only.
- **`memkraft doctor --check-updates`** — doctor now optionally checks PyPI and reports 🟢 up-to-date / 🟡 update available / 🔴 PyPI unreachable.
- **GitHub Actions auto-release** (`.github/workflows/release.yml`) — push a `vX.Y.Z` tag and CI builds, verifies, uploads to PyPI, and cuts a GitHub Release.

### Docs
- README: new "Staying up to date" section.
- Maintainers: PyPI publishes require `PYPI_API_TOKEN` repo secret.

### Compatibility
- Fully backward-compatible with 0.8.1. No public API changes; all 515 existing tests pass.

---

## [0.8.1] - 2026-04-17

### Added
- **`mk.init()` now returns `{"created": [...], "exists": [...], "base_dir": "..."}`** — quickstart examples actually work.
- **`memkraft agents-hint <target>` CLI** — copy-paste integration snippets for 6 targets: `claude-code`, `openclaw`, `openai`, `cursor`, `mcp`, `langchain`. Also supports `--format json` and `--base-dir` overrides.
- **`examples/` folder** — drop-in AGENTS.md block, OpenAI function-calling example, 10-line RAG loop.
- **`python -m memkraft.mcp`** — MCP stdio server exposing `remember`, `search`, `recall`, `link`. Requires `pip install 'memkraft[mcp]'`.
- **`memkraft watch`** — filesystem auto-reindex. Requires `pip install 'memkraft[watch]'`.
- **`memkraft doctor`** — health check for install + memory structure, with 🟢/🟡/🔴 icons and suggested fixes.

### Fixed
- README Quick Start example using `mk.init()` previously produced no observable side effect besides printing; now returns a structured dict so tests and scripts can branch on it.

### Changed
- `pyproject.toml` gains `[project.optional-dependencies]`: `mcp`, `watch`, and `all`.
- Package now ships `src/memkraft/prompts/templates/*.md` as package-data and includes `examples/` in sdist via `MANIFEST.in`.

### Compatibility
- Fully backward-compatible with 0.8.0. All 492 existing tests still pass; 23 new tests added (515 total).

---

## [0.8.0] — 2026-04-16

### The Memory Foundation

0.8.0 establishes the four subsystems that every later release builds on:
bitemporal facts, tier-based attention, reversible decay, and a wiki-link
graph. All four ship zero-dep and fully backward-compatible with 0.7.x.

### Added

- **Bitemporal Fact Layer** (`src/memkraft/bitemporal.py`) — facts carry
  a `valid_from` + optional `valid_to`, so entity memory becomes a
  time-travel ledger. Inline Markdown markers in
  `memory/facts/<slug>.md` keep the store git-diffable.
  - APIs: `fact_add`, `fact_at`, `fact_history`, `fact_invalidate`,
    `fact_list`, `fact_keys`.
- **Memory Tier Labels + Working Set** (`src/memkraft/tiers.py`) — three
  tiers drive retrieval priority: `core` (always recalled), `recall`
  (recalled on relevance), `archival` (cold storage). `working_set`
  assembles `core` + recently-accessed `recall` entities into an
  attention budget.
  - APIs: `tier_set`, `tier_of`, `tier_list`, `tier_promote`,
    `tier_demote`, `tier_touch`, `working_set`.
- **Reversible Decay + Tombstone** (`src/memkraft/decay.py`) —
  exponential weight decay for long-unaccessed entities, with
  tombstones that move stale files to `.memkraft/tombstones/` but keep
  them restorable. Accessing an entity restores weight; deletion is
  never destructive.
  - APIs: `decay_apply`, `decay_list`, `decay_restore`, `decay_run`,
    `decay_tombstone`.
- **Cross-Entity Link Graph + Backlinks** (`src/memkraft/links.py`) —
  `[[Wiki Link]]` patterns in any markdown file are parsed into a
  bidirectional graph. The filesystem is the database; rebuild with
  `link_scan`.
  - APIs: `link_scan`, `link_backlinks`, `link_forward`, `link_graph`,
    `link_orphans`.

### API surface added in 0.8.0

| API | Role |
|-----|------|
| `fact_add` | Bitemporal fact with `valid_from` / `valid_to` |
| `fact_at` | Query facts as of a given timestamp |
| `fact_history` | Full history of a fact key |
| `tier_set` | Set retrieval tier (`core` / `recall` / `archival`) |
| `working_set` | Build attention budget from core + recent recall |
| `decay_apply` | Apply reversible exponential decay |
| `decay_tombstone` | Soft-delete to `.memkraft/tombstones/` |
| `link_scan` | Rebuild the `[[wiki-link]]` graph index |
| `link_backlinks` | Query incoming links for an entity |

### Tests

409 → **492 passed** (+83 new in `tests/test_v080_bitemporal.py`).

### Upgrade

```bash
pip install --upgrade memkraft
```

### Compatibility

Fully backward-compatible with 0.7.x. No storage migration required:
all bitemporal state lives in frontmatter + inline Markdown markers in
the same plain files introduced in 0.5. Zero new dependencies.

---

## [0.7.0] — 2026-04-15

### Multi-Agent Auto Integration

0.7.0 turns MemKraft from a single-agent memory into a substrate for
coordinated multi-agent workflows. Channel context, task continuity,
and agent working memory (introduced in 0.6) become interoperable:
tasks can be delegated, working memory can be handed off, and context
injection is now tunable per-call.

### Added

- **`channel_update` modes** — `set` / `append` / `merge` for flexible
  channel-context updates without clobbering prior state.
- **`task_delegate()`** — track agent-to-agent task delegation with
  full history (who delegated what, to whom, when).
- **`agent_handoff()`** — transfer working memory + context between
  agents as a first-class operation.
- **`channel_tasks()`** — query recent tasks per channel with status
  filtering (e.g. `pending`, `in_progress`, `completed`).
- **`task_cleanup()`** — auto-archive completed tasks older than N
  days, keeping the active task set lean.
- **`agent_inject()` enhancements** — new `max_history` and
  `include_completed_tasks` options to shape the injected context
  block per sub-agent.

### API surface added in 0.7.0

| API | Role |
|-----|------|
| `task_delegate` | Record agent → agent task delegation |
| `agent_handoff` | Transfer working memory between agents |
| `channel_tasks` | Query tasks by channel + status |
| `task_cleanup` | Archive old completed tasks |

### Tests

357 → **409 passed** (+52 new in `tests/test_v070_multiagent.py`).

### Upgrade

```bash
pip install --upgrade memkraft
```

### Compatibility

Fully backward-compatible with 0.6.x. Zero new dependencies; all
multi-agent state reuses the `.memkraft/channels/`, `.memkraft/tasks/`,
and `.memkraft/agents/` directories introduced in 0.6.

---

## [0.6.1] — 2026-04-15

### Added

- **`agent_inject()`** promoted into the 0.6 public API surface,
  merging channel + task + agent context into a single prompt block.
- Documentation tightened around the channel / task / agent trio.

### Tests

**357 passed** (same as 0.6.0 — patch release).

### Compatibility

Fully backward-compatible with 0.6.0. Zero-dependency maintained.

---

## [0.6.0] — 2026-04-15

### Channel Context + Task Continuity + Agent Working Memory

0.6.0 introduces the three substrates that 0.7's multi-agent features
build on:

1. **Channel Context** — persistent per-channel state (e.g. one
   Telegram chat, one Slack thread) in `.memkraft/channels/`.
2. **Task Continuity** — a lifecycle register for work units
   (`start` → `update` → `complete`) in `.memkraft/tasks/`.
3. **Agent Working Memory** — per-agent persistent scratchpad in
   `.memkraft/agents/`, injectable into sub-agent prompts.

### Added

- **`channel_save` / `channel_load` / `channel_update`** — per-channel
  context persistence.
- **`task_start` / `task_update` / `task_complete` / `task_history` /
  `task_list`** — full task lifecycle tracking.
- **`agent_save` / `agent_load`** — per-agent persistent working
  memory.
- **`agent_inject()`** — merges channel + task + agent context into a
  single prompt-ready block.
- **CLI** — `channel-save` / `channel-load`, `task-start` /
  `task-update` / `task-list`, `agent-save` / `agent-load` /
  `agent-inject`.

### API surface added in 0.6.0

| API | Role |
|-----|------|
| `channel_save` / `channel_load` / `channel_update` | Channel state |
| `task_start` / `task_update` / `task_complete` | Task lifecycle |
| `task_history` / `task_list` | Task queries |
| `agent_save` / `agent_load` | Agent working memory |
| `agent_inject` | Merge channel + task + agent context |

### Tests

328 → **357 passed** (+29 new in `tests/test_v054_context.py`).

### Upgrade

```bash
pip install --upgrade memkraft
```

### Compatibility

Fully backward-compatible with 0.5.x. Zero-dependency maintained.

> 0.5.4 and 0.5.5 shipped the same channel/task/agent features as
> pre-release iterations before the feature set was frozen and
> re-tagged as 0.6.0.

---

## [0.5.1] — 2026-04-14

### Robustness Pass

0.5.1 hardens the Snapshots & Time Travel engine introduced in 0.5.0
for multi-agent usage (OpenClaw 2026-04 compatibility).

### Fixed

- **Symlink safety** — `_all_md_files()` skips symlinks to prevent
  circular traversal.
- **Race-safe `stat()` calls** — `OSError` guards across
  `health_check`, `diff`, `retro`, `open_loops`, `build_index`,
  `summarize`, `snapshot_diff`.
- **Collision-free snapshot IDs** — snapshot IDs now include a
  microsecond + UUID suffix
  (`SNAP-YYYYMMDD-HHMMSS-xxxxxx`), eliminating collisions when
  multiple agents snapshot within the same second.
- **Dynamic version in manifests** — snapshot manifests read
  `__version__` at runtime instead of a hardcoded string.
- **Snapshot memory cap** — per-file embedding in `include_content`
  is capped at 1 MB to prevent memory exhaustion on large workspaces.
- **Timezone normalisation** — `time_travel` normalises timezone-aware
  ISO timestamps to naive for cross-platform datetime comparison.

### Tests

**328 passed** (unchanged — stabilisation release, PyPI-deployed).

### Compatibility

Fully backward-compatible with 0.5.0. Zero-dependency maintained.

> 0.5.2 and 0.5.3 shipped the same robustness fixes as pre-release
> iterations; the consolidated, PyPI-tagged release is **0.5.1**.

---

## [0.5.0] — 2026-04-14

### Memory Snapshots & Time Travel

The foundational release. 0.5.0 turns a MemKraft workspace into a
point-in-time–queryable ledger: every memory state can be snapshotted,
diffed, and searched as of any past moment.

### Added

- **`snapshot()`** — point-in-time memory snapshots covering the full
  `memory/` tree.
- **`snapshot_list()`** — enumerate all snapshots.
- **`snapshot_diff()`** — compare two snapshots (added / removed /
  modified entities).
- **`time_travel()`** — search past memory states as of a given
  timestamp.
- **`snapshot_entity()`** — per-entity evolution tracking across
  snapshots.

### API surface added in 0.5.0

| API | Role |
|-----|------|
| `snapshot` | Create a point-in-time snapshot |
| `snapshot_list` | List snapshots |
| `snapshot_diff` | Diff two snapshots |
| `snapshot_entity` | Per-entity evolution timeline |
| `time_travel` | Query memory as of a past timestamp |

### Tests

277 → **328 passed** (+51 new in `tests/test_v050_snapshots.py`).

### Upgrade

```bash
pip install --upgrade memkraft
```

### Architecture note

0.5.0 cemented the design choice that every subsequent release
inherits: **the filesystem is the database.** Snapshots are plain
Markdown + manifest files, diffable with `git`, portable across
agents, and readable without MemKraft installed.

---

## Pre-0.5 history

0.2 – 0.4 were exploratory releases (Goal-Weighted Reconstructive
Memory, Feedback Loop + Confidence Levels + Applicability Conditions,
Debug Hypothesis Tracking). They are preserved in git history
(`git log --grep='v0\.[234]'`) but are not part of the 1.0 API
contract. 0.5.0 is the first version whose APIs still exist in 1.0.
