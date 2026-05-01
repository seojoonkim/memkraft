# ReasoningBank — MemKraft v2.7.1 Design

**Status:** Implementation spec  
**Author:** Zeon (subagent)  
**Date:** 2026-05-01  
**Target version:** 2.7.1 (additive patch, zero breaking changes)

---

## 1. Motivation

Agents repeat reasoning steps across sessions and repeat the same mistakes. MemKraft already
records *facts*, *decisions*, *incidents*, and *prompt evidence*, but none of those layers
captures **the agent's own step-by-step reasoning trajectory** — the chain of
(thought → action → outcome) that led to a success or a failure.

ReasoningBank fills that gap:

- **Trajectory log** — record every step of an agent's reasoning while it is happening.
- **Pattern extraction** — once a trajectory is finished, distill it into a reusable lesson
  (success pattern) or an anti-pattern (failure pattern).
- **Reasoning recall** — before the next task, retrieve the most relevant past trajectories
  (and their distilled lessons) to prime the agent.
- **Repeated-mistake detection** — when the same failure pattern occurs ≥ N times, surface it
  loudly so the agent (and the human reviewer) can break the loop.

The design follows MemKraft's existing rules:

- **Additive only.** No existing API signature changes.
- **Mixin pattern.** Lives in `src/memkraft/reasoning_bank.py`, registered at import time.
- **Stdlib only.** No new external dependencies.
- **Reuses existing infrastructure** — BM25 index, `_log_event`, search cache invalidation.

---

## 2. Public API

All methods are attached to `MemKraft` via `ReasoningBankMixin`. Six methods, each
keyword-friendly and forgiving with input.

```python
mk.trajectory_start(
    task_id: str,                 # caller-chosen ID. If empty → auto uuid.
    *,
    title: str = "",              # one-line description for browsing
    tags: str = "",                # comma-separated, optional
) -> dict
# Returns: {"task_id": str, "started_at": iso, "path": str}
# Side effect: creates .memkraft/trajectories/<task_id>.jsonl with a `start` record.

mk.trajectory_log(
    task_id: str,
    step: int,                    # caller-managed monotonically increasing integer
    *,
    thought: str = "",
    action: str = "",
    outcome: str = "",
    metadata: dict | None = None, # arbitrary JSON-serializable extras
) -> dict
# Returns: {"task_id": str, "step": int, "appended_at": iso}
# Side effect: appends a `step` JSONL record. Auto-starts the trajectory if missing.

mk.trajectory_complete(
    task_id: str,
    *,
    status: str = "success",      # one of: success | failure | partial
    lesson: str = "",             # human-readable distilled lesson (1-3 lines)
    pattern_signature: str = "",  # optional explicit signature; auto-derived if empty
    tags: str = "",
) -> dict
# Returns: {"task_id": str, "status": str, "lesson": str,
#           "pattern_signature": str, "completed_at": iso, "duplicate_count": int}
# Side effect:
#   1) Append a `complete` JSONL record.
#   2) Upsert .memkraft/patterns.json under (status, signature) bucket.
#   3) On failure with duplicate_count ≥ MIN_REPEAT_WARN (default 2),
#      log a high-importance event via mk.log_event(...) — only if log_event exists.

mk.reasoning_recall(
    query: str,
    *,
    top_k: int = 3,
    status: str = "",             # filter: "" (all) | "success" | "failure" | "partial"
    min_score: float = 0.0,       # token-overlap relevance threshold
) -> list[dict]
# Returns: list of {"task_id", "title", "status", "lesson",
#                   "pattern_signature", "score", "completed_at",
#                   "tags", "step_count", "path"}.
# Source: completed trajectories only. Lessons + titles + tags + signature are
# tokenised and matched against `query` using the same Jaccard scorer used by
# prompt_evidence (stopwords-aware if mk.stopwords exists).

mk.reasoning_patterns(
    *,
    status: str = "",             # "" | "success" | "failure" | "partial"
    min_count: int = 1,           # only patterns seen at least N times
    top_k: int = 20,
) -> list[dict]
# Returns: list of {"signature", "status", "count", "first_seen", "last_seen",
#                   "task_ids", "lessons"}.
# Source: .memkraft/patterns.json. Sorted by count DESC, then last_seen DESC.

mk.trajectory_get(task_id: str) -> dict
# Returns: {"task_id", "title", "status", "lesson", "tags",
#           "started_at", "completed_at", "steps": [...], "path"}.
# Reads the JSONL file end-to-end and returns the full reconstructed view.
# Raises FileNotFoundError if missing.
```

### Interaction with existing APIs

- `mk.search()` / `mk.search_v2()` are **not** modified. Reasoning trajectories live under
  `.memkraft/` and are intentionally invisible to the markdown corpus index — they're a
  *meta layer*, not document memory. Recall is via `mk.reasoning_recall()`.
- `mk.log_event(...)` is called (best-effort) when a repeat failure is detected, with
  `tags="reasoning-bank,repeat-failure"` and `importance="high"`. This automatically lights
  up in retros and dashboards.
- Cache invalidation: trajectory APIs do **not** mutate document memory, so they don't
  participate in `_bump_cache_generation`. (The trajectory store is its own world.)

---

## 3. Storage Layout

All state lives under `<base_dir>/.memkraft/` so it never pollutes user-facing markdown.

```
<base_dir>/.memkraft/
  trajectories/
    <task_id>.jsonl        ← append-only, one JSON record per line
    <task_id>.jsonl
    ...
  patterns.json            ← single JSON file, atomic write via tmp+rename
```

### `trajectories/<task_id>.jsonl` schema

Each line is a JSON object with a `kind` discriminator. Order is start → step* → complete.

**`start` record (exactly one, first line):**
```json
{
  "kind": "start",
  "task_id": "deploy-fanfic-2026-05-01",
  "title": "Vercel 배포 후 Ready 확인 누락 점검",
  "tags": ["deploy", "vercel"],
  "started_at": "2026-05-01T11:30:00+09:00",
  "schema_version": 1
}
```

**`step` records (zero or more):**
```json
{
  "kind": "step",
  "task_id": "deploy-fanfic-2026-05-01",
  "step": 1,
  "thought": "git push 완료, vercel ls로 상태 확인 필요",
  "action": "exec: vercel ls",
  "outcome": "● Ready (production)",
  "metadata": {"duration_ms": 3200},
  "ts": "2026-05-01T11:31:05+09:00"
}
```

**`complete` record (exactly one, last line):**
```json
{
  "kind": "complete",
  "task_id": "deploy-fanfic-2026-05-01",
  "status": "success",
  "lesson": "Vercel 배포 후 ● Ready 확인까지 끝낸 뒤에만 형한테 보고.",
  "pattern_signature": "vercel-deploy-ready-check",
  "tags": ["deploy", "vercel"],
  "completed_at": "2026-05-01T11:32:00+09:00"
}
```

Concurrency: append mode + line-buffered writes. Two appenders won't corrupt JSONL
because each `write` is a complete line; readers tolerate missing/partial lines (skipped).

### `patterns.json` schema

```json
{
  "schema_version": 1,
  "patterns": {
    "failure::missing-vercel-ready-check": {
      "status": "failure",
      "signature": "missing-vercel-ready-check",
      "count": 3,
      "first_seen": "2026-04-12T00:00:00+09:00",
      "last_seen": "2026-05-01T11:32:00+09:00",
      "task_ids": ["task-a", "task-b", "task-c"],
      "lessons": [
        "git push 직후 보고하지 말 것",
        "vercel ls로 ● Ready 본 다음에만 완료 선언",
        "...latest 3 deduped lessons..."
      ]
    },
    "success::vercel-deploy-ready-check": {
      "...": "..."
    }
  }
}
```

- Top-level keys are always `"<status>::<signature>"` to avoid collisions across statuses.
- `lessons[]` keeps the **last 3 distinct** lessons (FIFO with dedup) to bound size.
- `task_ids[]` keeps the **last 50** ids (FIFO).
- Atomic write: write to `patterns.json.tmp` then `os.replace`.

---

## 4. Pattern Signature Algorithm

When `trajectory_complete` is called without an explicit `pattern_signature`, derive one
from `(lesson, tags)` using a deterministic, stopword-aware token bag:

```
def _derive_signature(lesson: str, tags: list[str]) -> str:
    # 1. Lowercase, ASCII-fold light, strip punctuation.
    # 2. Tokenize on whitespace + Korean particles (re-use core._strip_josa
    #    when available; otherwise plain split).
    # 3. Drop stopwords (use self.stopwords if loaded; else built-in tiny EN/KO set).
    # 4. Take top 4 tokens by length DESC, then alphabetical for stability.
    # 5. Join tokens with "-".
    # If the result is empty → signature = "unsignatured-<sha1(lesson)[:8]>".
```

Properties:
- **Deterministic.** Same lesson always yields same signature.
- **Stable across stopword updates.** Falls back to a sha1 hash if degenerate.
- **Human-readable.** e.g. `"vercel-deploy-ready-check"`.
- **Dedup-friendly.** Two trajectories with the same lesson collapse to one pattern bucket.

The implementation uses `hashlib.sha1` only as a tie-breaker when token extraction fails;
it is **not** a security boundary and intentionally short.

---

## 5. Reasoning Recall Algorithm

```
def reasoning_recall(self, query, *, top_k=3, status="", min_score=0.0):
    # 1. Walk .memkraft/trajectories/*.jsonl
    # 2. For each file, read only the start + complete records (skip steps for speed).
    # 3. If no complete record → ignore (in-flight trajectory).
    # 4. Optional status filter.
    # 5. Score: jaccard(query_tokens, doc_tokens) where doc_tokens =
    #          tokens(title) ∪ tokens(lesson) ∪ tokens(tags) ∪ tokens(signature).
    #    Stopword-aware via self.stopwords if present.
    # 6. Apply min_score threshold; sort DESC by score, then last_seen DESC.
    # 7. Return up to top_k results.
```

- O(N) on number of trajectories. Acceptable up to ~10k completed trajectories per repo;
  a future v2.8 can bolt on the BM25 index. No premature optimization.
- step_count is computed cheaply: `wc -l` minus 2 (start + complete) clamped to ≥ 0.

---

## 6. Repeat-Failure Detection

`trajectory_complete(..., status="failure")` flow:

```
1. Append `complete` record to JSONL.
2. Load patterns.json (if missing, create with empty patterns).
3. Bucket key = "failure::<signature>".
4. If bucket exists:
     bucket.count += 1
     bucket.last_seen = now
     append task_id (cap 50) and lesson (dedup, cap 3)
   else:
     create bucket with count=1, first_seen=last_seen=now.
5. Write patterns.json atomically.
6. If bucket.count ≥ MIN_REPEAT_WARN (default 2) AND mk has log_event:
     mk.log_event(
       event=f"⚠️ Repeated failure pattern '{signature}' ({count}x): {lesson}",
       tags="reasoning-bank,repeat-failure",
       importance="high",
     )
7. Return enriched result dict (includes duplicate_count = bucket.count).
```

Constants live at module top: `MIN_REPEAT_WARN = 2`, `MAX_LESSONS_PER_PATTERN = 3`,
`MAX_TASK_IDS_PER_PATTERN = 50`.

---

## 7. Edge Cases & Guarantees

| Case | Handling |
|------|----------|
| `task_id` with path separators or empty | `_safe_task_id()` slugifies (allowed: `[A-Za-z0-9_.-]`); empty → `uuid.uuid4().hex[:12]` |
| `trajectory_log` before `trajectory_start` | Auto-start with empty title — never raise |
| `trajectory_complete` called twice | Second call appends a new `complete` record but does NOT double-count the pattern (idempotent on `(task_id, status, signature)`) |
| Corrupt JSONL line (e.g. partial write) | Skipped silently in readers |
| Missing `patterns.json` | Treated as empty; first complete creates it |
| `status` not in `{success, failure, partial}` | Coerced to `"partial"` |
| `tags` as list vs comma-string | Both accepted; normalized to list internally and persisted as list |
| Concurrent writers | Append-only JSONL is safe per-line; `patterns.json` uses atomic rename — last writer wins (acceptable: counts may drift by ±1 under heavy concurrency, never corrupt) |
| Backward compat | All trajectories store `schema_version: 1`; future upgrades read by version |

---

## 8. Test Plan (≥ 10 cases, all in `tests/test_reasoning_bank.py`)

1. `test_trajectory_start_creates_jsonl` — file exists, contains start record.
2. `test_trajectory_log_appends_step` — multiple steps appended in order.
3. `test_trajectory_log_auto_starts` — log without explicit start works.
4. `test_trajectory_complete_writes_pattern_bucket` — patterns.json updated.
5. `test_signature_derivation_deterministic` — same lesson → same signature.
6. `test_signature_fallback_when_degenerate` — empty/stopwords-only lesson still gets a stable signature.
7. `test_reasoning_recall_finds_relevant_lesson` — query matches a completed trajectory.
8. `test_reasoning_recall_status_filter` — filtering by `status="failure"` excludes successes.
9. `test_reasoning_recall_min_score_threshold` — sub-threshold matches dropped.
10. `test_reasoning_patterns_sorted_by_count` — most frequent pattern first.
11. `test_repeat_failure_bumps_count_and_logs_event` — duplicate_count grows; high-importance event written via `log_event`.
12. `test_trajectory_get_round_trip` — full reconstruction of (start, steps, complete).
13. `test_trajectory_complete_idempotent_on_duplicate_signature` — second complete with same signature/status doesn't double-bump count.
14. `test_corrupt_jsonl_line_is_skipped` — unparseable line ignored, surrounding records still loaded.
15. `test_storage_layout_is_under_dot_memkraft` — files live under `<base>/.memkraft/`, never in user markdown.

Plus regression: `pytest -x` must show **0 regressions** (baseline 1192 passed / 3 skipped).

---

## 9. Non-Goals (deferred)

- **No** automatic LLM-generated lesson summarization. Caller writes the lesson; we just store it.
- **No** vector embedding. Token Jaccard is sufficient for v2.7.1; revisit in v2.8 with the BM25 index.
- **No** UI / CLI command. Programmatic API only this round; CLI shim can land in v2.7.2 if needed.
- **No** cross-repo synchronization. Single-repo scope.
- **No** pruning policy beyond per-pattern caps. Trajectories accumulate; users can `rm` `.memkraft/trajectories/` to reset.

---

## 10. Migration / Upgrade

- Pure additive — `pip install --upgrade memkraft` is sufficient.
- First call to any trajectory API auto-creates `.memkraft/trajectories/`.
- No DB schema changes; no migrations.
- Rollback: deleting `.memkraft/trajectories/` and `.memkraft/patterns.json` returns to a clean state.
