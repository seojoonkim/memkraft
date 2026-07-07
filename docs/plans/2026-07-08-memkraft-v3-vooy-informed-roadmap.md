# MemKraft v3 Vooy-Informed Roadmap Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Fold the vooy Memory master spec into MemKraft’s v3 roadmap without turning MemKraft into a vooy-specific consumer app.

**Architecture:** Keep MemKraft local-first, stdlib-first, and agent-facing. Borrow vooy’s lifecycle primitives—compiled truth, timeline, last interaction, session candidate overlay, resolver, governance, and action-oriented context—while leaving consumer domains such as contacts, places, products, meals, mobile UX, and SaaS RLS to vooy/product layers.

**Tech Stack:** Python stdlib-first MemKraft core, existing markdown/JSONL storage under `<base_dir>`, existing `.memkraft/` sidecars, optional `memkraft[embedding]`, Memory Gym JSON gates, pytest.

---

## 1. Readout: What the vooy spec changes

The vooy Memory spec does not invalidate the current MemKraft v3 plan. It sharpens it.

Current MemKraft v3 plan says:

```text
retrieval-time memory → lifecycle-computed memory
```

The vooy spec supplies the missing operational primitives for that lifecycle:

```text
CAPTURE → EXTRACT → RESOLVE → COMPILE → INDEX → RETRIEVE → INJECT → ACT → FEEDBACK → DECAY/ARCHIVE
```

For MemKraft, this means 3.0.0 should not be released merely because Memory Gym, hybrid retrieval, and Provenance Core exist. A true 3.0.0 should prove that memory has become an adaptive lifecycle:

1. Memory is captured as source-backed events or candidates.
2. Candidates are resolved into current truth or conflicts.
3. Current truth and timeline are precomputed.
4. Context is compiled from typed, provenanced, utility-aware memory products.
5. Agent outcomes affect future ranking or injection.
6. Governance operations—why, delete, export, audit, do-not-remember—are first-class.

## 2. Scope boundary

### 2.1 Borrow into MemKraft core

These are product-neutral primitives that strengthen MemKraft itself:

- **Compiled Truth + Timeline**: current stable facts separated from event history.
- **Last Interaction Index**: generic `subject_id → latest_event` index for fast recall.
- **Session Candidate Overlay**: read-your-writes guarantee for async/candidate memory.
- **Candidate / Resolver Lifecycle**: `NEW`, `DUPLICATE`, `UPDATE`, `CORRECTION`, `CONTRADICTION`, `REFINEMENT`, `REJECT`, `CANDIDATE_REVIEW` verdicts.
- **Action-Oriented Context**: compile context with open tasks, next actions, workflow hints, risks, and sources.
- **Tool/API Procedural Memory Lite**: success/failure/fallback patterns from tool calls without storing credentials or raw sensitive payloads.
- **Governance Minimum**: source tracing, delete/export/audit/do-not-remember.
- **Memory Gym Expansion**: gates for temporal, provenance, last-interaction, session overlay, resolver, and context quality.

### 2.2 Keep outside MemKraft core

These belong to vooy or product integrations, not MemKraft core:

- Consumer-specific Person dossier fields like birthdays, contact methods, relationship strength, and UI labels.
- Places, products, foods, purchases, visits, meals as first-class app domains.
- Mobile app, memory browser, notification UX, recall card UI.
- Cloud SaaS RLS, KMS, multi-tenant Postgres policies.
- Agent-role product architecture such as People Agent, Product Agent, Privacy Agent.
- TypeScript/HTTP endpoint shapes copied directly from vooy.

MemKraft should expose primitives that let vooy implement those domains cleanly.

## 3. Version strategy

### 2.12.0 — already released

Status: released.

Scope already shipped:

- Memory Gym foundation.
- Hybrid retrieval safety and canonical path fusion fix.
- Provenance Core: `provenance_record(...)` and `why(...)`.
- Hermes Agent installability docs and wheel smoke.

Interpretation:

```text
2.12.0 = v3 foundation release, not true 3.0.0.
```

### 2.13.0 — Lifecycle Foundation

Goal: make the memory lifecycle explicit and safe before adding higher-level context compilation.

Release theme:

```text
Capture and resolve memory safely before compiling it.
```

Major capabilities:

1. Memory Gym expansion beyond search.
2. Candidate memory and resolver verdicts.
3. Session overlay for read-your-writes.
4. Generic Last Interaction Index alpha.
5. `sleep(dry_run=True)` wrapper over existing consolidation.
6. Provenance coverage for sleep/candidate outputs.

### 2.14.0 — Context Compiler + Outcome Loop

Goal: turn stored/resolved memory into task-ready context and let outcomes affect future use.

Release theme:

```text
Use memory as a task substrate, not just search results.
```

Major capabilities:

1. `compile_context(...)` alpha.
2. Current truth + timeline + procedural + next-action context sections.
3. `report_outcome(...)` and utility events.
4. Tool/API procedural memory lite.
5. Local governance minimum: delete/export/audit/do-not-remember.
6. Memory Gym gates for context quality and outcome learning.

### 3.0.0 — Memory OS Release

Goal: ship the first complete adaptive memory lifecycle.

Release theme:

```text
MemKraft v3 turns memory from passive recall into an adaptive lifecycle.
```

3.0.0 is justified only if these claims are true and benchmarked:

1. **It remembers better**: recall, temporal correctness, conflict/stale surfacing, and procedural reuse improve or do not regress.
2. **It thinks cheaper**: compiled context uses fewer tokens than raw top-k for equal or better task performance.
3. **It improves with use**: outcome feedback changes future ranking/injection, and sleep creates provenanced derived records.

---

# Phase A — 2.13.0 Lifecycle Foundation

## Milestone A0: Baseline and spec alignment

**Objective:** Preserve the released 2.12.0 baseline and document the vooy-informed deltas.

**Files:**
- Modify: `docs/V3_PARADIGM_PLAN.md`
- Create: `docs/plans/2026-07-08-memkraft-v3-vooy-informed-roadmap.md`
- Modify: `docs/bench/V3_BASELINE.md`

**Tasks:**

1. Add a short “vooy-informed lifecycle primitives” section to `docs/V3_PARADIGM_PLAN.md`.
2. Link this roadmap from the v3 plan.
3. Record that 2.12.0 is a v3 foundation release, not 3.0.0.
4. Re-run current Memory Gym baseline:
   ```bash
   PYTHONPATH=src python3 benchmarks/gym/run.py --scenario search_recall --sizes 20,100,300 --top-k 5 --gate --out /tmp/memkraft-v213-prebaseline.json
   ```
5. Run full tests:
   ```bash
   PYTHONPATH=src python3 -m pytest -q
   ```

**Exit criteria:**

- Existing Gym gate remains green.
- No production behavior changes.
- Roadmap and baseline docs clearly distinguish 2.12.0, 2.13.0, 2.14.0, and 3.0.0.

## Milestone A1: Memory Gym lifecycle scenarios

**Objective:** Expand Memory Gym from search-only safety to lifecycle safety.

**Files:**
- Modify: `benchmarks/gym/scenarios.py`
- Modify: `benchmarks/gym/run.py`
- Modify: `benchmarks/gym/gates.py`
- Test: `tests/test_memory_gym.py`
- Create if useful: `benchmarks/gym/scenarios_lifecycle.py`

**Required scenarios:**

1. `search_recall` — existing baseline.
2. `session_overlay_recall` — newly captured candidate is visible immediately.
3. `last_interaction` — latest event per subject is returned correctly.
4. `resolver_verdicts` — duplicate/update/conflict/correction verdicts are stable.
5. `provenance_coverage` — derived/candidate records carry sources.
6. `context_packet_shape` — compiled context sections are structurally valid once Phase B begins.

**TDD steps:**

1. Write failing tests for scenario registry accepting new scenario names.
2. Write failing tests for invalid scenario parameters returning structured gate failures, not tracebacks.
3. Implement minimal scenario stubs returning metrics.
4. Add impossible-threshold tests proving non-zero exit on gate failure.
5. Add real scenario data after primitives are implemented.

**Verification commands:**

```bash
PYTHONPATH=src python3 -m pytest tests/test_memory_gym.py -q
PYTHONPATH=src python3 benchmarks/gym/run.py --scenario session_overlay_recall --gate --out /tmp/memkraft-session-overlay-gym.json
PYTHONPATH=src python3 benchmarks/gym/run.py --scenario last_interaction --gate --out /tmp/memkraft-last-interaction-gym.json
```

**Exit criteria:**

- Gym handles lifecycle scenario names.
- Malformed metrics fail safely.
- Existing search gate still passes.

## Milestone A2: Candidate memory sidecar

**Objective:** Add a local candidate memory layer without disturbing existing markdown/fact APIs.

**Files:**
- Create: `src/memkraft/candidates.py`
- Modify: `src/memkraft/__init__.py`
- Test: `tests/test_candidates.py`

**Storage:**

```text
<base_dir>/.memkraft/candidates.jsonl
```

**Minimal public API:**

```python
mk.remember_candidate(
    text: str,
    *,
    source: dict | None = None,
    entity_hint: str | None = None,
    session_id: str | None = None,
    expires_at: str | None = None,
) -> dict

mk.list_candidates(session_id: str | None = None, include_expired: bool = False) -> list[dict]
```

**Record shape:**

```json
{
  "candidate_id": "cand_...",
  "text": "...",
  "summary": "...",
  "source": {"channel": "manual", "captured_at": "..."},
  "entity_hint": "...",
  "session_id": "...",
  "status": "candidate",
  "created_at": "...",
  "expires_at": "...",
  "provenance_id": "prov_..."
}
```

**TDD steps:**

1. Test that `remember_candidate` appends one JSONL record and returns its id.
2. Test id generation is filesystem/path-safe.
3. Test corrupt JSONL lines are skipped by `list_candidates`.
4. Test expired candidates are hidden by default.
5. Test candidate creation calls or records provenance when source is present.

**Exit criteria:**

- Candidate layer is additive.
- No existing `remember`, `search`, or ReasoningBank behavior changes.
- Source-backed candidate records can be traced with `why(...)` or equivalent provenance reference.

## Milestone A3: Session candidate overlay

**Objective:** Guarantee read-your-writes for newly captured candidate memory.

**Files:**
- Modify: `src/memkraft/candidates.py`
- Modify: `src/memkraft/search.py` or add helper module used by search/compile later
- Test: `tests/test_session_overlay.py`

**Minimal public API:**

```python
mk.session_overlay(session_id: str, query: str = "", *, top_k: int = 5) -> list[dict]
```

**Behavior:**

- Session candidates are searched before long-term compiled truth.
- Overlay results are clearly marked:
  ```json
  {"status": "candidate", "memory_state": "session_overlay"}
  ```
- They are not treated as active facts.
- Expired candidates are skipped.

**TDD steps:**

1. Write failing test: create candidate in session `s1`, immediately search overlay, result appears.
2. Write failing test: same candidate does not appear for session `s2` unless global include is requested.
3. Write failing test: expired candidate is excluded.
4. Implement token-overlap search first; do not introduce embeddings.
5. Add Memory Gym `session_overlay_recall` scenario using this API.

**Exit criteria:**

- Same-session candidate recall works without async extraction.
- Memory Gym scenario passes.

## Milestone A4: Resolver dry-run alpha

**Objective:** Establish deterministic candidate-to-fact verdicts before automatic writes.

**Files:**
- Create: `src/memkraft/resolver.py`
- Modify: `src/memkraft/__init__.py`
- Test: `tests/test_resolver.py`

**Minimal public API:**

```python
mk.resolver_dry_run(candidate: dict, existing: list[dict] | None = None) -> dict
```

**Verdicts:**

```python
NEW
DUPLICATE
UPDATE
CORRECTION
CONTRADICTION
REFINEMENT
REJECT
CANDIDATE_REVIEW
```

**Initial implementation scope:**

- Stdlib deterministic rules only.
- No LLM dependency.
- Operate on simple dicts with keys:
  - `subject`
  - `predicate`
  - `object_value`
  - `valid_from`
  - `confidence`
  - `source_quote`

**TDD steps:**

1. Test `NEW` when no same subject/predicate exists.
2. Test `DUPLICATE` for normalized same value.
3. Test `UPDATE` for newer valid_from and different value.
4. Test `CORRECTION` when source text says correction or candidate has `correction=True`.
5. Test `CONTRADICTION` when valid windows overlap with conflicting values.
6. Test `REJECT` when required source_quote is missing.
7. Add Memory Gym `resolver_verdicts` scenario.

**Exit criteria:**

- Resolver has stable structured output:
  ```json
  {"verdict": "UPDATE", "requires_user_confirmation": false, "explanation": "..."}
  ```
- Missing source quote cannot become active memory.

## Milestone A5: Last Interaction Index alpha

**Objective:** Add generic fast lookup for the most recent event per subject.

**Files:**
- Create: `src/memkraft/last_interaction.py`
- Modify: `src/memkraft/__init__.py`
- Test: `tests/test_last_interaction.py`

**Storage:**

```text
<base_dir>/.memkraft/last_interactions.json
```

**Minimal public API:**

```python
mk.record_interaction(
    subject_id: str,
    *,
    subject_type: str = "entity",
    interaction_type: str = "event",
    summary: str,
    occurred_at: str,
    source_id: str | None = None,
    next_actions: list[str] | None = None,
    related_subjects: list[str] | None = None,
) -> dict

mk.last_interaction(subject_id: str) -> dict | None
```

**Update rule:**

- Monotonic by `occurred_at`.
- Older events must not overwrite newer ones.
- Equal timestamp tie-break by source captured time or interaction id.

**TDD steps:**

1. Test recording a first interaction.
2. Test newer interaction replaces older.
3. Test older interaction does not overwrite newer.
4. Test `next_actions` and `related_subjects` persist.
5. Test corrupt store gracefully returns empty or valid rows.
6. Add Memory Gym `last_interaction` scenario.

**Exit criteria:**

- p95 target for small local stores should be measured and documented.
- Latest interaction is source-linked.

## Milestone A6: `sleep(dry_run=True)` wrapper and provenance-linked plan

**Objective:** Turn existing `consolidate()` into the beginning of a first-class sleep API.

**Files:**
- Modify: `src/memkraft/consolidation.py`
- Modify: `src/memkraft/cli.py`
- Test: `tests/test_sleep.py`

**Minimal public API:**

```python
mk.sleep(strategy: str = "auto", dry_run: bool = True) -> dict
```

**CLI:**

```bash
memkraft sleep --base-dir /path/to/memory --dry-run
memkraft sleep --base-dir /path/to/memory --apply
```

**Output shape:**

```json
{
  "dry_run": true,
  "strategy": "auto",
  "planned_writes": [],
  "planned_updates": [],
  "provenance_coverage": 1.0,
  "details": []
}
```

**TDD steps:**

1. Test `mk.sleep(dry_run=True)` does not write files.
2. Test output contains planned writes/updates arrays even when empty.
3. Test invalid strategy fails with clear ValueError or CLI usage error.
4. Test generated observation plan includes source spans or `provenance="unknown"` explicitly.
5. Test CLI dry-run exits 0 and prints JSON or stable text.

**Exit criteria:**

- `sleep --dry-run` previews changes safely.
- New sleep-derived records cannot be silently source-less.

---

# Phase B — 2.14.0 Context Compiler + Outcome Loop

## Milestone B1: Compiled Truth and Timeline views

**Objective:** Add explicit current-truth and event-timeline artifacts for context compilation.

**Files:**
- Create: `src/memkraft/compiled_truth.py`
- Create: `src/memkraft/timeline.py`
- Modify: `src/memkraft/__init__.py`
- Test: `tests/test_compiled_truth.py`
- Test: `tests/test_timeline.py`

**Storage:**

```text
<base_dir>/.memkraft/compiled_truth.jsonl
<base_dir>/.memkraft/timeline.jsonl
```

**Minimal public API:**

```python
mk.compile_truth(subject_id: str | None = None, *, dry_run: bool = False) -> dict
mk.current_truth(subject_id: str, *, domain: str | None = None) -> list[dict]
mk.record_event(subject_id: str, event: dict) -> dict
mk.timeline(subject_id: str, *, limit: int = 20) -> list[dict]
```

**TDD steps:**

1. Test current truth chooses active/latest fact over superseded fact.
2. Test timeline returns events sorted newest first or documented order.
3. Test compiled truth is regenerable from fact/event data.
4. Test source ids are preserved.
5. Test stale facts can be marked with warning metadata.

**Exit criteria:**

- `current_truth` and `timeline` become stable internal APIs for `compile_context`.

## Milestone B2: Context Compiler alpha

**Objective:** Create the primary agent-facing API for v3.

**Files:**
- Create: `src/memkraft/context_compile.py`
- Modify: `src/memkraft/__init__.py`
- Test: `tests/test_context_compile.py`

**Public API:**

```python
mk.compile_context(
    task: str,
    *,
    budget: int = 8000,
    objective: str = "solve",
    subject_id: str | None = None,
    session_id: str | None = None,
    include_provenance: bool = True,
) -> dict
```

**Output sections:**

```json
{
  "task": "...",
  "budget": 8000,
  "sections": {
    "current_truth": [],
    "timeline": [],
    "session_candidates": [],
    "procedural_lessons": [],
    "open_tasks_next_actions": [],
    "conflicts_stale_warnings": [],
    "sources": []
  },
  "markdown": "...",
  "tokens_estimate": 1234,
  "miss": false
}
```

**TDD steps:**

1. Test empty memory returns `miss=True` and no hallucinated facts.
2. Test subject-specific current truth appears before generic search hits.
3. Test session candidates are included and labeled as candidates.
4. Test budget cap is respected by item boundary, not arbitrary JSON slicing.
5. Test provenance citations appear when `include_provenance=True`.
6. Test no source-less item is rendered as certain truth.

**Exit criteria:**

- Agents can call `compile_context` instead of raw `search` for task startup.
- Markdown output is compact, deterministic, and source-aware.

## Milestone B3: Outcome utility events

**Objective:** Close the loop between memory use and future ranking.

**Files:**
- Create: `src/memkraft/utility.py`
- Modify: `src/memkraft/reasoning_bank.py`
- Modify: `src/memkraft/context_compile.py`
- Test: `tests/test_memory_utility.py`

**Storage:**

```text
<base_dir>/.memkraft/utility_events.jsonl
```

**Public API:**

```python
mk.report_outcome(
    usage_id: str,
    *,
    success: bool | None = None,
    reward: float | None = None,
    evidence: str | None = None,
) -> dict
```

**TDD steps:**

1. Test `compile_context` emits a `usage_id` and item ids.
2. Test `report_outcome` appends utility event.
3. Test invalid reward values are rejected or clamped deterministically.
4. Test successful items get a conservative ranking boost in future compile.
5. Test failed items are not hard-deleted, only downranked or warned.

**Exit criteria:**

- Outcome feedback changes future context ordering in a measurable, conservative way.

## Milestone B4: Tool/API procedural memory lite

**Objective:** Borrow vooy’s API success/failure memory as a product-neutral procedural layer.

**Files:**
- Create: `src/memkraft/tool_memory.py`
- Modify: `src/memkraft/__init__.py`
- Modify: `src/memkraft/context_compile.py`
- Test: `tests/test_tool_memory.py`

**Public API:**

```python
mk.log_tool_call(
    task_category: str,
    tool: str,
    *,
    operation: str | None = None,
    purpose: str | None = None,
    status: str,
    latency_ms: int | None = None,
    cost_estimate: float | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
    privacy_level: str = "private",
    source_id: str | None = None,
) -> dict

mk.tool_patterns(task_category: str) -> dict
```

**Security constraints:**

- Never store raw API keys, bearer tokens, passwords, cookies, or credential-like values.
- Store input/output summaries only.
- `privacy_level="secret"` payloads should be rejected or recorded only as redacted references.

**TDD steps:**

1. Test successful calls increase success count.
2. Test failed calls record error code/summary.
3. Test repeated failure creates known failure pattern after threshold.
4. Test fallback order prefers higher success rate, tie-breaking lower latency.
5. Test credential-looking strings are redacted or rejected.
6. Test context compiler includes relevant tool patterns under procedural lessons.

**Exit criteria:**

- Repeated tool failures can influence future agent context.
- No secrets enter memory files.

## Milestone B5: Local governance minimum

**Objective:** Make MemKraft safe enough to call a memory operating system.

**Files:**
- Create: `src/memkraft/governance.py`
- Modify: `src/memkraft/cli.py`
- Modify: `src/memkraft/__init__.py`
- Test: `tests/test_governance.py`

**Public API:**

```python
mk.audit_log(action: str, *, target_id: str | None = None, purpose: str | None = None, metadata: dict | None = None) -> dict
mk.export_memory(*, format: str = "markdown") -> str | dict
mk.forget(scope: dict, *, mode: str = "soft_delete", reason: str = "user_request") -> dict
mk.do_not_remember(pattern: str, *, scope: str = "global") -> dict
```

**CLI:**

```bash
memkraft export --base-dir /path/to/memory --format markdown
memkraft forget --base-dir /path/to/memory --id mem_...
memkraft audit --base-dir /path/to/memory --limit 20
```

**TDD steps:**

1. Test audit log appends stable JSONL rows.
2. Test export includes source ids and excludes soft-deleted records by default.
3. Test forget marks record deleted or tombstoned and future search/compile hides it.
4. Test do-not-remember pattern prevents candidate creation for matching text.
5. Test audit log is written for forget/export operations.

**Exit criteria:**

- User can see, export, and remove memory locally.
- Agents have a programmable rule for “do not remember.”

## Milestone B6: Context quality Memory Gym gates

**Objective:** Prove context compiler benefits before claiming v3.

**Files:**
- Modify: `benchmarks/gym/scenarios.py`
- Create: `benchmarks/gym/scenarios_context.py`
- Modify: `benchmarks/gym/gates.py`
- Test: `tests/test_memory_gym.py`

**Metrics:**

```json
{
  "raw_topk_tokens": 2000,
  "compiled_tokens": 1200,
  "required_facts_recalled": 0.95,
  "source_coverage": 1.0,
  "candidate_labels_correct": 1.0,
  "stale_warnings_present": 1.0
}
```

**Exit criteria:**

- Compiled context uses fewer tokens than raw top-k for equivalent required-fact coverage on fixture tasks.
- Source coverage remains high.

---

# Phase C — 3.0.0 Memory OS Release

## Milestone C1: First-class `sleep()` digestion

**Objective:** Promote sleep from wrapper to real offline memory digestion.

**Files:**
- Modify: `src/memkraft/consolidation.py`
- Modify: `src/memkraft/compiled_truth.py`
- Modify: `src/memkraft/timeline.py`
- Modify: `src/memkraft/provenance.py`
- Test: `tests/test_sleep.py`

**Required behavior:**

- Sleep creates or refreshes derived memory views.
- Every derived record has source provenance or explicit `provenance="unknown"`.
- Dry-run shows exact planned writes.
- Apply mode writes atomically.
- Corrupt or partial sleep outputs do not break search/compile.

**Exit criteria:**

```bash
PYTHONPATH=src python3 -m pytest tests/test_sleep.py tests/test_provenance.py -q
memkraft sleep --base-dir /tmp/memkraft-v3-smoke/memory --dry-run
```

## Milestone C2: End-to-end lifecycle replay benchmark

**Objective:** Prove capture → resolve → compile → act/outcome → improved compile.

**Files:**
- Create: `benchmarks/gym/scenarios_lifecycle_replay.py`
- Modify: `benchmarks/gym/run.py`
- Test: `tests/test_memory_gym.py`

**Fixture flow:**

1. Capture source text about a person/project/tool failure.
2. Create candidates.
3. Resolve into active truth/conflict.
4. Compile context for a task.
5. Report successful/failed outcome.
6. Compile again.
7. Verify useful memory is promoted or harmful memory is downranked.

**Exit criteria:**

- Lifecycle replay gate passes.
- Ranking difference before/after outcome is measurable.

## Milestone C3: 3.0 API hardening

**Objective:** Stabilize public APIs and document compatibility.

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/V3_PARADIGM_PLAN.md`
- Create: `docs/V3_API.md`
- Test: all relevant tests

**Stable APIs for 3.0.0:**

- `remember_candidate`
- `resolver_dry_run`
- `record_interaction`
- `last_interaction`
- `sleep`
- `current_truth`
- `timeline`
- `compile_context`
- `report_outcome`
- `log_tool_call`
- `tool_patterns`
- `audit_log`
- `export_memory`
- `forget`
- `do_not_remember`
- `provenance_record`
- `why`

**Compatibility rule:**

- Existing `remember`, `search`, `search_v2`, `search_smart`, `search_hybrid`, ReasoningBank APIs remain available.
- 3.0.0 adds primary lifecycle APIs but should avoid unnecessary breakage.

## Milestone C4: Release gates

**Objective:** Only publish 3.0.0 if the v3 promise is proven.

**Required commands:**

```bash
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 benchmarks/gym/run.py --scenario search_recall --gate --out /tmp/memkraft-v3-search.json
PYTHONPATH=src python3 benchmarks/gym/run.py --scenario session_overlay_recall --gate --out /tmp/memkraft-v3-overlay.json
PYTHONPATH=src python3 benchmarks/gym/run.py --scenario last_interaction --gate --out /tmp/memkraft-v3-last-interaction.json
PYTHONPATH=src python3 benchmarks/gym/run.py --scenario resolver_verdicts --gate --out /tmp/memkraft-v3-resolver.json
PYTHONPATH=src python3 benchmarks/gym/run.py --scenario context_quality --gate --out /tmp/memkraft-v3-context.json
PYTHONPATH=src python3 benchmarks/gym/run.py --scenario lifecycle_replay --gate --out /tmp/memkraft-v3-lifecycle.json
python3 -m build
python3 -m twine check dist/*
```

**3.0.0 release criteria:**

- Search baseline no regression.
- Candidate/session overlay works.
- Last interaction lookup works and is measured.
- Resolver verdicts deterministic.
- Source/provenance coverage high.
- Context compiler uses fewer tokens than raw top-k for fixture tasks.
- Outcome feedback changes future context ranking.
- Governance APIs are tested.
- Fresh wheel install smoke passes.
- Hermes provider can use installed package without `source_path`.

---

# 4. Immediate next implementation order

Recommended next PRs after this planning document:

1. `docs: fold vooy memory lifecycle into v3 roadmap`
   - Update `docs/V3_PARADIGM_PLAN.md` only.
   - No production code.

2. `test: expand memory gym lifecycle scenario registry`
   - Add scenario names and structured gate failure tests.
   - Stubs only where primitives are missing.

3. `feat: add candidate memory sidecar`
   - `remember_candidate`, `list_candidates`, provenance linkage.

4. `feat: add session candidate overlay`
   - read-your-writes guarantee and Gym scenario.

5. `feat: add resolver dry-run alpha`
   - deterministic verdicts and Gym scenario.

6. `feat: add last interaction index alpha`
   - generic event recording, monotonic update, Gym scenario.

7. `feat: add sleep dry-run wrapper`
   - CLI/API and provenance-linked plan output.

Do not start with consumer domains like Person/Place/Product/Food. Those are vooy-level schemas. MemKraft should first make the underlying lifecycle reliable.

# 5. Risks and guardrails

## Risk: turning MemKraft into vooy app backend

Guardrail:

- Keep domain model generic: `subject_id`, `subject_type`, `event`, `candidate`, `fact`, `context section`.
- Do not bake contact fields, restaurant fields, product lifecycle fields, or consumer UX into core.

## Risk: claiming 3.0.0 too early

Guardrail:

- 3.0.0 requires lifecycle replay and context quality gates.
- If `compile_context`, `sleep`, and outcome feedback are not integrated, release as 2.x.

## Risk: source-less derived memories

Guardrail:

- New derived records must include source spans or explicit `provenance="unknown"`.
- Memory Gym provenance coverage gate must fail if silent source loss occurs.

## Risk: async memory feels broken

Guardrail:

- Session overlay must ship before any async extraction story.
- Candidate memories should be labeled as candidates in context.

## Risk: performance regression

Guardrail:

- Keep fast paths stdlib and sidecar-based.
- Embeddings remain optional.
- Search baseline and repeated-query cache gates must stay green.

# 6. Final versioning recommendation

Keep `2.12.0` as-is.

Use:

- `2.13.0`: Lifecycle Foundation.
- `2.14.0`: Context Compiler + Outcome Loop.
- `3.0.0`: true Memory OS once lifecycle replay, sleep, context compiler, outcome learning, last interaction, session overlay, provenance, and governance are integrated and benchmark-gated.

Short statement for release positioning:

```text
MemKraft 2.12 introduced the v3 foundation.
MemKraft 2.13 makes the lifecycle explicit.
MemKraft 2.14 makes memory task-ready.
MemKraft 3.0 ships the complete adaptive memory OS.
```
