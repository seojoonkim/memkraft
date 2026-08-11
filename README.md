**v3.4.0**

<div align="center">

# MemKraft

**Local-first, plain-Markdown compound knowledge and accountable self-improvement for AI agents.**

Remember sourced facts. Retrieve bounded context. Act with any model or agent. Report the result. Let evidence improve the next retrieval.

[![PyPI](https://img.shields.io/pypi/v/memkraft.svg)](https://pypi.org/project/memkraft/)
[![Python](https://img.shields.io/pypi/pyversions/memkraft.svg)](https://pypi.org/project/memkraft/)
[![Tests](https://img.shields.io/github/actions/workflow/status/seojoonkim/memkraft/gym-gate.yml?label=tests)](https://github.com/seojoonkim/memkraft/actions/workflows/gym-gate.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[Quickstart](#quickstart) · [The loop](#the-accountable-memory-loop) · [Python API](#python-api) · [CLI](#cli-reference) · [MCP](docs/mcp-setup.md)

</div>

MemKraft keeps its human-facing knowledge in files you can read, diff, edit, and version. Its core install uses the Python standard library, makes no model calls, and needs no API key. Your agent supplies the intelligence; MemKraft supplies persistent knowledge, provenance, lifecycle controls, and a feedback ledger.

<p align="center">
  <img src="assets/readme/agent-loop.svg" width="100%" alt="MemKraft loop: remember sourced facts, retrieve budgeted context, act, report an outcome, and improve the next retrieval">
</p>

## Why MemKraft

Most agent memory stops at **store → search**. MemKraft connects memory to what happened after recall:

- **Own the source of truth.** Entity pages, decisions, timelines, and notes are plain Markdown; local JSONL sidecars hold operational records such as canonical events and outcomes.
- **Keep memory accountable.** Canonical facts require a source or provenance, compiled truth is reconstructable, and retrieval preserves source links.
- **Learn from use.** `compile_context()` returns a stable `usage_id`; `report_outcome()` records success or failure and deterministically adjusts later context ordering.
- **Govern explicitly.** Forgetting, do-not-remember policies, tombstones, dry-run lifecycle operations, audit logs, and fail-closed reads are part of the system boundary.
- **Stay model-agnostic.** Use the CLI, Python API, MCP server, framework hints, or your own tool wrapper. MemKraft itself does not call an LLM.

> **Storage boundary:** Markdown is the human-facing knowledge source of truth. MemKraft also maintains local `.memkraft/` indexes, snapshots, canonical event logs, policies, and outcome records. Some governance operations hide or compact active local records; they do not promise deletion from Git history, backups, filesystem snapshots, or external copies.

## Quickstart

Requires **Python 3.9+**. For an isolated CLI install:

```bash
pipx install memkraft
memkraft init
memkraft agents-hint claude-code >> AGENTS.md
```

Or install into the current Python environment:

```bash
pip install memkraft
```

`memkraft init` creates `./memory/` by default (or `$MEMKRAFT_DIR`). The generated resolver, templates, entity directories, and local state are ready for an agent to use.

```bash
memkraft track "Acme API" --type project --source "project docs"
memkraft update "Acme API" --info "Retries must use exponential backoff" --source "ADR-007"
memkraft search "retry policy"
```

Scaffold an integration-specific project instead:

```bash
memkraft init --template claude-code
memkraft init --template cursor
memkraft init --template mcp
memkraft init --template rag
memkraft init --template minimal
memkraft templates list
```

Templates are create-only and idempotent: re-running a template does not overwrite existing files.

## The accountable memory loop

The host agent performs the action; MemKraft records what was recalled and what happened next.

```python
from memkraft import MemKraft

mk = MemKraft("./memory")

# 1. Remember — canonical events require source or provenance.
mk.append_event(
    "deploy-agent",
    "rollback_rule",
    "Rollback when the 5xx rate stays above 2% for five minutes",
    source="runbook:production",
)
mk.compile_truth(dry_run=False)

# 2. Retrieve — fit provenance-bearing context into a hard token budget.
context = mk.compile_context(
    task="Deploy the API safely",
    budget=500,
    pinned_sources=["runbook:production"],
)
usage_id = context["usage_id"]

# 3. Act — your agent/model uses `context`; MemKraft does not dispatch it.
# result = agent.run(task="Deploy the API safely", context=context)

# 4. Report — append an outcome linked to the exact context usage.
mk.report_outcome(
    usage_id,
    outcome="success",
    reward=0.8,
    metadata={"idempotency_key": "deploy-2026-08-03"},
)

# 5. Improve — later compilation incorporates decayed outcome utility.
next_context = mk.compile_context(task="Deploy the API safely", budget=500)
```

The feedback update is bounded to ±20%, rewards are clamped to `[-1, 1]`, and utility decays with a 30-day half-life. Reporting is append-only and can be made idempotent. Unknown usage IDs are rejected; pins, budgets, tombstones, provenance, and governance policies remain authoritative. See [`docs/V3_API.md`](docs/V3_API.md) for the exact contract.

A second, Markdown-native tuning loop tracks prompts and skills as first-class entities:

```python
mk.prompt_register(
    "release-review",
    path="skills/release-review/SKILL.md",
    owner="platform",
    tags=["release", "quality"],
)

mk.prompt_eval(
    "release-review",
    iteration=1,
    scenarios=[{
        "name": "missing-migration",
        "description": "Release changes a storage contract",
        "requirements": [{"item": "flags migration risk", "critical": True}],
    }],
    results=[{
        "scenario": "missing-migration",
        "success": True,
        "accuracy": 100,
        "tool_uses": 3,
        "duration_ms": 1200,
        "unclear_points": [],
        "discretion": [],
    }],
)

past_evidence = mk.prompt_evidence("release-review", "migration risk")
verdict = mk.convergence_check("release-review", window=2)
```

Every iteration leaves inspectable decisions and links. MemKraft stores the report; your host agent runs the evaluation.

## How it works

### Knowledge and lifecycle

- **Compiled truth + timeline:** current state and the history that produced it.
- **Bitemporal facts:** record transaction time and fact-validity time.
- **Tiers:** `core`, `recall`, and `archival` control context priority.
- **Links:** `[[wiki-links]]` and backlinks connect entity pages.
- **Reversible decay:** stale memories can be deprioritized without immediate destruction.
- **Snapshots and time travel:** compare stored states and search a captured past view.
- **Sleep:** deterministic truth compilation; preview by default and apply explicitly.
- **Candidates:** session-scoped preview memory can be reviewed before durable promotion.

### Retrieval

The canonical entry point is:

```python
results = mk.search("retry policy", mode="smart", top_k=10)
```

Supported modes are `legacy`, `v2`, `smart`, and `hybrid`; the default remains `legacy` for backward compatibility. The older named methods `search_v2()`, `search_smart()`, and `search_hybrid()` remain compatibility aliases but emit `DeprecationWarning`; use `search(..., mode=...)` in new code.

Other retrieval tools include fuzzy search, brain-first lookup, multi-hop agentic search, progressive disclosure, goal/context-aware re-ranking, wiki-link traversal, optional local embeddings, query-focused evidence compilation, and fail-closed numeric aggregation.

```python
hits = mk.agentic_search(
    "What failed during the last API rollout?",
    context="prepare today's deployment",
    file_back=True,
)

evidence = mk.compile_evidence_context(
    "What changed in the retry policy?",
    results=hits,
    top_k=10,
    budget=800,
)
```

`compile_evidence_context()` and `aggregate_numeric_evidence()` are preview APIs. Numeric aggregation only confirms explicit sum, count, or duration operations when units, provenance, and scope are unambiguous; otherwise it returns a non-success status rather than a partial answer.

## Integrate an agent

### Framework hints

`agents-hint` prints Markdown or JSON snippets for supported hosts:

```bash
memkraft agents-hint claude-code
memkraft agents-hint openclaw
memkraft agents-hint cursor
memkraft agents-hint openai
memkraft agents-hint mcp
memkraft agents-hint langchain
```

See [`examples/`](examples/) for a minimal RAG flow, OpenAI function tools, and Claude Code guidance.

### MCP

Install the optional dependency and run the stdio server:

```bash
pip install 'memkraft[mcp]'
python -m memkraft.mcp
```

Validate the local setup or run an isolated remember→search→recall smoke test:

```bash
memkraft mcp doctor
memkraft mcp test
```

Configuration examples for Claude Desktop and other MCP clients are in [`docs/mcp-setup.md`](docs/mcp-setup.md).

### Hermes Agent

Hermes Agent includes a MemKraft memory-provider plugin. Install MemKraft in the same environment and configure the profile:

```yaml
memory:
  provider: memkraft
plugins:
  memkraft:
    base_dir: $HERMES_HOME/memkraft-memory
    prefetch_top_k: 5
```

`plugins.memkraft.source_path` is only needed for an editable/source checkout. A normal wheel install imports `memkraft` from the active Python environment.

```bash
HERMES_HOME=/path/to/profile hermes memory status
HERMES_HOME=/path/to/profile hermes chat -Q --toolsets memory -q 'Call memkraft_status.'
```

## Python API

The 3.x lifecycle contract is documented in [`docs/V3_API.md`](docs/V3_API.md). The tables below keep the broader, long-lived API discoverable; preview surfaces are labeled separately.

### Stable 3.x lifecycle core

| Method | Purpose |
| --- | --- |
| `append_event(subject_id, key, value, source=...\|provenance=...)` | Append a sourced canonical event |
| `compile_truth(dry_run=True)` | Preview or build canonical compiled truth |
| `current_truth(subject_id)` | Read the applied truth view for one subject |
| `sleep(strategy="default", dry_run=True)` | Preview or apply a deterministic lifecycle transaction |
| `forget(target, dry_run=True)` | Preview or append a tombstone operation |
| `compile_context(task, budget, ...)` | Produce bounded, provenance-bearing context and a `usage_id` |
| `report_outcome(usage_id, outcome, ...)` | Append feedback for a recorded context usage |

`track`, `update`, `search`, `why`, and `export_memory` also remain public. Destructive lifecycle actions default to dry-run.

### Entities, facts, and organization

| Method | Purpose |
| --- | --- |
| `init(path="")` | Create the memory directory structure |
| `track(name, entity_type="person", source="")` | Start a tracked entity |
| `update(name, info, source="manual")` | Append sourced information to an entity |
| `brief(name, save=False, file_back=False)` | Compile an entity brief |
| `list_entities()` | List tracked entities |
| `tier_set(name, tier)` / `promote(name, tier)` | Set `core`, `recall`, or `archival` priority |
| `fact_add(...)` | Add a bitemporal fact |
| `links(name)` | Show backlinks for an entity |
| `suggest_links()` | Suggest missing wiki-links |

### Search and evidence

| Method | Purpose |
| --- | --- |
| `search(query, fuzzy=False, top_k=None, mode="legacy", ...)` | Canonical search entry point |
| `agentic_search(query, max_hops=2, context="", file_back=False)` | Decompose, traverse links, and re-rank |
| `lookup(query, brain_first=False, full=False)` | Stop after sufficient high-relevance results unless full retrieval is requested |
| `query(query="", level=1, ...)` | Progressive disclosure: index, sections, or full text |
| `compile_evidence_context(query, ...)` | Build provenance-preserving evidence under a hard budget *(preview)* |
| `aggregate_numeric_evidence(query, ...)` | Compose explicit sum/count/duration evidence or fail closed *(preview)* |

### Audit, maintenance, and history

| Method | Purpose |
| --- | --- |
| `health_check()` | Run memory assertions and return a score |
| `dream(date=None, dry_run=False, resolve_conflicts=False)` | Run legacy maintenance checks |
| `decay(days=90, dry_run=False)` | Flag stale facts with type-aware decay |
| `dedup(dry_run=False)` | Find and merge duplicate facts |
| `resolve_conflicts(strategy="newest", dry_run=False)` | Resolve detected contradictions |
| `snapshot(label="", include_content=False)` | Capture a point-in-time manifest |
| `snapshot_diff(snapshot_a, snapshot_b="")` | Compare snapshots or a snapshot with live state |
| `time_travel(query, snapshot_id="", date="")` | Search a captured past state |
| `timeline(subject_id=None, ...)` | Read the canonical event history |
| `audit_log(action=None, subject=None, limit=None)` | Read governance audit records |
| `export_memory(include_tombstoned=False)` | Export visible canonical memory |

### Agent continuity and scientific debugging

| Method | Purpose |
| --- | --- |
| `channel_save` / `channel_load` | Persist per-channel context |
| `task_start` / `task_update` / `task_list` | Track task state and history |
| `agent_save` / `agent_load` / `agent_inject` | Persist and inject agent working context |
| `start_debug(description)` | Begin an OBSERVE → HYPOTHESIZE → EXPERIMENT → CONCLUDE session |
| `log_hypothesis` / `log_evidence` | Record theories and test results |
| `reject_hypothesis` / `confirm_hypothesis` | Preserve failed and confirmed approaches |
| `search_debug_sessions` / `search_rejected_hypotheses` | Reuse past debugging evidence |

### Preview and secondary APIs

The following are public but may gain additive fields or stricter validation in minor releases. Pin the MemKraft version if you persist their schemas as an external contract:

- Candidate/session memory: `remember_candidate`, `list_candidates`, `session_overlay`, `forget_candidates`
- Governance and compaction: `do_not_remember`, `compact_memory`, `truth_status`
- Claim pipeline: `extract_claims`, `resolver_dry_run`
- Interaction views: `record_interaction`, `last_interaction`
- Evidence helpers: `compile_evidence_context`, `aggregate_numeric_evidence`
- Local live sync: `live_sync_apply`, `live_sync_events`, `live_sync_freshness`, `live_sync_repair`
- Optional embedding maintenance: `embedding_sync_path`, `embedding_index_state`

See [`docs/V3_API.md`](docs/V3_API.md) for exact semantics and [`docs/MIGRATIONS.md`](docs/MIGRATIONS.md) for 2.13.x → 3.x migration and rollback guidance.

## CLI reference

Run `memkraft <command> --help` for complete options.

### Capture and retrieval

| Command | Purpose |
| --- | --- |
| `init [--path DIR] [--template NAME]` | Initialize memory or apply a scaffold |
| `templates list` | List built-in scaffolds |
| `track NAME [--type T] [--source S]` | Track an entity |
| `update NAME --info INFO [--source S]` | Append entity information |
| `extract TEXT [--source S] [--dry-run] [--confidence C]` | Extract entities and facts |
| `detect TEXT [--source S] [--dry-run]` | Detect EN/KR/CN/JP entities |
| `search QUERY [--fuzzy] [--file-back]` | Search memory files |
| `agentic-search QUERY [--max-hops N] [--context C] [--file-back]` | Multi-step retrieval |
| `lookup QUERY [--brain-first] [--full]` | Brain-first lookup |
| `query [QUERY] [--level 1\|2\|3]` | Progressive disclosure query |
| `brief NAME [--save] [--file-back]` | Generate an entity brief |
| `links NAME` | Show backlinks |

### Lifecycle, maintenance, and audit

| Command | Purpose |
| --- | --- |
| `sleep [--dry-run\|--apply]` | Preview or apply canonical truth compilation |
| `cognify [--dry-run] [--apply]` | Route inbox content into structured pages |
| `health-check` | Run memory assertions |
| `dream [--dry-run] [--resolve-conflicts]` | Run legacy maintenance |
| `resolve-conflicts [--strategy S] [--dry-run]` | Resolve contradictions |
| `decay [--days N] [--dry-run]` | Flag stale facts |
| `dedup [--dry-run]` | Merge duplicates |
| `summarize [NAME] [--max-length N]` | Condense entity pages |
| `diff` | Show changes since maintenance |
| `open-loops [--dry-run]` | Find TODO, FIXME, and pending items |
| `doctor [--check-updates] [--fix] [--dry-run]` | Check installation and memory structure |
| `stats [--export json\|csv\|human]` | Report workspace statistics |

### History, agents, and integrations

| Command | Purpose |
| --- | --- |
| `snapshot [--label L] [--include-content]` | Capture a snapshot |
| `snapshot-list` / `snapshot-diff` | List or compare snapshots |
| `time-travel QUERY [--snapshot ID] [--date YYYY-MM-DD]` | Search a past snapshot |
| `snapshot-entity NAME` | Show entity evolution across snapshots |
| `debug ...` | Run the hypothesis-tracking workflow |
| `channel-save`, `channel-load`, `channel-update` | Manage channel context |
| `task-start`, `task-update`, `task-list`, `task-delegate` | Manage task continuity |
| `agent-save`, `agent-load`, `agent-inject`, `agent-handoff` | Manage agent working memory |
| `agents-hint TARGET [--format markdown\|json]` | Generate integration instructions |
| `mcp doctor` / `mcp test` | Validate MCP readiness or run a local round trip |
| `watch [--path PATH] [--once]` | Watch and re-index; requires `memkraft[watch]` |
| `freshness [--path DIR] [--repair] [--json]` | Diagnose or rebuild derived indexes from canonical Markdown |
| `selfupdate [--dry-run]` | Upgrade via pip when a newer PyPI version exists |

<details>
<summary><strong>Daily CLI recipe</strong></summary>

```bash
# Capture
memkraft extract "Acme API retries use exponential backoff." --source "ADR-007"
memkraft track "Acme API" --type project --source "project docs"

# Retrieve and feed the retrieval event back to the timeline
memkraft search "retry policy" --fuzzy --file-back
memkraft brief "Acme API"

# Inspect before changing lifecycle state
memkraft sleep
memkraft sleep --apply
memkraft doctor

# Preserve a checkpoint
memkraft snapshot --label before-release
memkraft stats --export json
```

</details>

## Optional dependencies

The core package has no required third-party runtime dependencies. Install only the integrations you need:

```bash
pip install 'memkraft[mcp]'        # Model Context Protocol server
pip install 'memkraft[watch]'      # filesystem watcher
pip install 'memkraft[schedule]'   # scheduled lifecycle jobs
pip install 'memkraft[embedding]'  # local sentence-transformer retrieval
pip install 'memkraft[all]'        # all optional groups, including benchmark tooling
```

## On-disk shape

A default workspace includes human-editable Markdown plus local operational state:

```text
memory/
├── .memkraft/        # indexes, canonical events, policies, outcomes, snapshots
├── RESOLVER.md       # classification rules
├── TEMPLATES.md      # page templates
├── entities/         # people, organizations, projects, concepts
├── live-notes/       # actively tracked entities
├── decisions/        # decisions and rationale
├── originals/        # verbatim captures
├── inbox/            # unclassified input
├── meetings/         # briefs and notes
├── tasks/            # work-in-progress context
├── sessions/         # structured event logs
└── debug/            # hypothesis-driven debugging sessions
```

Derived indexes and compiled views are not authoritative. They can be rebuilt from source records; policy and tombstone checks remain part of reads.

## Benchmarks and evidence

### Memory Gym

The repository's deterministic, offline Memory Gym exercises lifecycle replay, provenance, governance, context budgets, outcome-linked ordering, and other release contracts. CI invokes each registered scenario with its advertised gate. See [`benchmarks/gym/`](benchmarks/gym/) and the [Gym workflow](.github/workflows/gym-gate.yml).

### LongMemEval

The repository contains historical LongMemEval work, but it should be read with its limits intact. The durable evidence note records a **96.0% semantic self-judge result on a seeded 50-question oracle subset** from a dirty 3.0.1 pre-final candidate. It was not a full-dataset run, not an independent-model judgment, and not final 3.1.0 validation. Automatic contains match on that run was **68.0%**.

Read the complete setup, hashes, later experiments, and interpretation limits in [`docs/bench/longmemeval-3.0.1.md`](docs/bench/longmemeval-3.0.1.md). MemKraft 3.1.0 makes no new external benchmark accuracy claim.

### 3.1.0 performance scope

Version 3.1.0 optimizes `current_truth()` by replacing repeated full-event scans with a per-call signature index, while retaining the legacy equality scan as a compatibility fallback. The release note reports local synthetic single-subject results and explicitly does **not** present them as universal latency claims. See [`docs/releases/3.1.0.md`](docs/releases/3.1.0.md) for methodology, measurements, validation, and limits.

### 3.2.0 local-first live sync

Version 3.2.0 replaces watcher's search-ping side effect with explicit path invalidation, records compact provenance-linked change envelopes, reports canonical-to-derived freshness, repairs disposable indexes from Markdown, and updates an existing optional embedding index one document at a time. Markdown remains canonical, BM25 remains the default retriever, and no dependency was added to the core. See [`docs/LIVE_SYNC.md`](docs/LIVE_SYNC.md) and the [release notes](docs/releases/3.2.0.md).

## Safety and operational notes

- `sleep` and `forget` are dry-run by default; use explicit apply calls for writes.
- `cognify` recommends destinations unless `--apply` is supplied.
- Canonical events require non-empty source or provenance.
- Corrupt canonical event, policy, or compiled snapshots fail closed rather than reviving hidden data.
- `do_not_remember` and tombstones govern visibility; they are not guarantees of physical erasure from external copies.
- `selfupdate` changes the installed package. In agent automation, ask for human approval before upgrading.
- Local Markdown can contain sensitive information. Apply normal filesystem permissions, backup, and repository-access controls.

The full threat model is in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## Versioning and upgrades

Current version: **3.4.0**.

```bash
pipx upgrade memkraft
# or
pip install --upgrade memkraft

memkraft --version
```

No migration command or Markdown rewrite is required for 3.4.0. The execution, evaluation, improvement, and project-state stores are additive and lazy-created; 3.2.x ignores them on rollback. For API compatibility boundaries, read [`docs/V3_API.md`](docs/V3_API.md); for migrations and rollback, read [`docs/MIGRATIONS.md`](docs/MIGRATIONS.md).

## 📝 Changelog

### [v3.4.0](https://github.com/seojoonkim/memkraft/releases/tag/v3.4.0) (current)

MemKraft 3.4.0 consolidates the runtime-neutral MKEP/0 execution Preview, Evaluation Corpus, Continual Improvement Ledger Preview, Project State Contract, and a fail-closed single-release-lineage gate. Read the [release notes](docs/releases/3.4.0.md).

### [v3.2.0](https://github.com/seojoonkim/memkraft/releases/tag/v3.2.0)

MemKraft 3.2.0 adds local-first live sync, freshness diagnostics and repair, provenance-linked file change events, and optional single-path embedding updates without changing canonical storage or default retrieval. Read the [release notes](docs/releases/3.2.0.md).

### [v3.1.0](https://github.com/seojoonkim/memkraft/releases/tag/v3.1.0)

The 3.1.0 release strengthens accountable retrieval, lifecycle controls, and performance while preserving the local-first Markdown storage model. Read the [release notes](docs/releases/3.1.0.md) or browse the complete [`CHANGELOG.md`](CHANGELOG.md).

## Contributing

Issues and pull requests are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the development workflow.

## Inspirations and credits

MemKraft draws on ideas from tiered agent memory, temporal knowledge, scientific debugging, structured wikis, and evidence-driven iteration. Project-specific acknowledgements and links are preserved below.

<details>
<summary><strong>View inspirations</strong></summary>

- [Karpathy auto-research](https://x.com/karpathy/status/1906697764923920553) — evidence-based autonomous research
- [Shen Huang's debug-hypothesis skill](https://github.com/LichAmnesia/lich-skills/tree/main/skills/debug-hypothesis) — scientific debugging
- [Letta / MemGPT](https://github.com/letta-ai/letta) — tiered memory architecture
- [mem0](https://github.com/mem0ai/mem0) — agent memory extraction and retrieval patterns
- [Zep](https://github.com/getzep/zep) — temporal memory and entity extraction
- [MemoryWeaver](https://github.com/pchaganti/gx-memory-weaver) — dialectic synthesis and reconstruction
- [Karpathy llm-wiki](https://x.com/karpathy/status/2042079355925164424) — wiki-style structured knowledge for LLMs

</details>

## License

[MIT](LICENSE)

<div align="center">

[GitHub](https://github.com/seojoonkim/memkraft) · [PyPI](https://pypi.org/project/memkraft/) · [Issues](https://github.com/seojoonkim/memkraft/issues)

</div>
