# MemKraft v2.8 Refactor Plan

Date: 2026-05-02  
Branch: `refactor/v2.8-comprehensive`  
Status: **Pending approval**  
Constraint: **No public API signature changes. All existing 1254 tests must remain green.**

## 0. Baseline used for this plan

- Tests: 1254 passed, 3 skipped
- Untracked write 100x total: 1.17ms
- Tracked write 100x total: 9.27ms
- Tracked search 100x total: 1,165.60ms
- Coverage (branch, source=src/memkraft): ~70% statements

## 0b. Guiding Principles

1. Measure first, change second.
2. Preserve external behavior exactly.
3. Use internal-only decomposition (private mixins/helpers/internal modules).
4. Make changes revert-friendly and reviewable.
5. Prioritize search-path gains first because that is the dominant runtime hotspot.

## 1. Workstreams

### WS-A. `core.py` internal decomposition

- **Purpose**: structure
- **Expected effect**:
  - Lower cognitive load
  - Easier future debugging
  - Better targeted optimization
- **Approach**:
  - Split internal responsibilities into private modules/mixins without changing `MemKraft` public class shape
  - Candidate internal buckets:
    - `core_search_helpers.py` (tokenization, candidate read, scoring helpers)
    - `core_detection_helpers.py` (regex/entity/conflict helpers)
    - `core_lifecycle_helpers.py` (dream/retro/health/snapshot helpers)
  - `core.py` remains facade
- **Risk**: medium
  - Import wiring bugs
  - Missed method aliasing
- **Rollback**: high
  - Revert branch commits
- **Priority**: **P0**

### WS-B. Hot-path regex precompilation

- **Purpose**: speed
- **Expected effect**:
  - Reduce redundant regex compilation cost in `search`, `_search_tokens`, detection helpers
- **Approach**:
  - Move regex literals to module-level compiled constants
  - Share compiled patterns across calls
- **Risk**: low
- **Rollback**: high
- **Priority**: **P0**

### WS-C. Candidate read caching

- **Purpose**: speed
- **Expected effect**:
  - Reduce repeated `read_text` / open / decode workloads for recently-read note files
- **Approach**:
  - Add bounded LRU/TTL cache keyed by resolved file path + mtime/size
  - Invalidate on write paths (`update`, tier/frontmatter writes, attachment writes)
  - Keep cache private/internal only
- **Risk**: medium
  - Stale reads if invalidation misses a mutation path
- **Rollback**: high
- **Priority**: **P0**

### WS-D. Structured logging instrumentation

- **Purpose**: debuggability
- **Expected effect**:
  - Trace search/update lifecycle without changing behavior
- **Approach**:
  - Add `logging.getLogger("memkraft.<module>")`
  - DEBUG breadcrumbs:
    - query normalized form
    - candidate count
    - files read
    - final top-K scores
  - Convert library-side `print` to logger where appropriate
  - Keep CLI surface printing intact
- **Risk**: low
- **Rollback**: high
- **Priority**: **P0**

### WS-E. Search-path complexity reduction

- **Purpose**: structure + speed
- **Expected effect**:
  - Easier optimization later
  - Reduce branching complexity
- **Approach**:
  - Break `search` into smaller internal steps:
    - query normalization
    - candidate collection
    - scoring
    - rerank/fusion
  - Extract helper functions from `_search_tokens`, `_detect_regex`, scoring loops
- **Risk**: medium-high
  - Behavior-sensitive area
- **Rollback**: high
- **Priority**: **P1**
- **Gate**: expand search regression tests first

### WS-F. Targeted regression test expansion

- **Purpose**: safety
- **Expected effect**:
  - Reduce refactor risk for search and mutation paths
- **Approach**:
  - Add tests for:
    - exact match
    - fuzzy match
    - alias resolved match
    - multi-token query
    - empty result path
    - update then search consistency
    - cache invalidation after write
- **Risk**: low
- **Rollback**: high
- **Priority**: **P1**

### WS-G. Other complexity hotspots

- **Purpose**: structure
- **Targets**:
  - `personamem.build_context`
  - `convergence.convergence_check`
  - `prompt_tune.prompt_eval`
- **Approach**:
  - Extract helper functions only
  - No external behavior change
- **Risk**: medium
- **Priority**: **P2**

## 2. Suggested Execution Order

1. **WS-F** first (safety net)
2. **WS-B** and **WS-D** together (cheap wins)
3. **WS-C** (requires careful invalidation + tests)
4. **WS-A** (structural refactor, reviewable chunked PRs)
5. **WS-E** (search refactor behind tests)
6. **WS-G** last

## 3. Success Criteria

- `pytest tests/` remains green: **1254 passed**
- No change to public `MemKraft` method signatures
- Measured improvement in:
  - search latency (target: reduce cumulative search time by 20-40% in bench script)
  - reduced redundant regex work in profile
- No new flaky tests
- Logger instrumentation available for search/update debugging

## 4. Recommended Non-goals (for this phase)

- Rewrite storage layer
- Introduce new external API surface
- Change CLI behavior
- Replace mixin pattern entirely
- Add embedding dependency into default search path

## 5. Approval Gate

Before any code changes:
- Confirm which workstreams are approved
- Confirm whether test expansion is approved first (recommended)
- Confirm whether `core.py` decomposition can proceed immediately or should wait behind perf-only changes

