# Project Memory Compiler Preview Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build an opt-in, deterministic compiler that turns existing project authorities into provenance-preserving, rebuildable context snapshots without becoming a second authority.

**Architecture:** Pure normalization and reduction sit between read-only adapters and an owned atomic snapshot store. Authority selection may rank cited sources but cannot create facts. Public-safe filtering happens before serialization and is verified again over the complete output envelope.

**Tech Stack:** Python 3.9/3.12 stdlib, MemKraft append-only stores, Git object database, JSON/JSONL, Markdown, pytest.

**Status:** Proposed after MemKraft 3.4.1 is publicly verified

**Release:** MemKraft 3.x opt-in Preview

---

## 1. Non-negotiable contract

1. Git, MemKraft records, configured project cards, and bound State Contract observations remain authoritative inputs.
2. PMC output is derived evidence. It cannot approve, promote, deploy, mutate a source, or claim authority above cited inputs.
3. Every snapshot is disposable and rebuildable. Existing MemKraft stores and MKEP/0 behavior remain unchanged.
4. Required-input loss, schema mismatch, conflict loss, path escape, privacy uncertainty, or resource-limit overflow fails closed.
5. Compiler core performs no network access, shell execution, LLM inference, or wall-clock reads.
6. Python 3.9 and 3.12 are the supported matrix.

## 2. Owned output and schema contract

```text
.memkraft/project-memory/
  .memkraft-pmc-owned
  manifest.json
  snapshots/<snapshot_id>/
    project.json
    facts.jsonl
    decisions.jsonl
    releases.jsonl
    risks.jsonl
    context.md
    diagnostics.json
```

The ownership marker contains `compiler_schema` and `project_id`. `clean` refuses any directory without an exactly matching marker; `--force` cannot bypass marker ownership. Every adapter excludes the resolved output root. Configuration fails if an input root is equal to or inside the output root, or if exclusion cannot be proved after symlink resolution.

`manifest.json`, every snapshot file, and `diagnostics.json` carry explicit schema versions. Readers reject unknown newer schemas without partial parsing. Supported local filesystems must provide same-filesystem atomic rename and directory fsync; NFS and filesystems without those guarantees are unsupported in Preview.

## 3. Canonical configuration

Strict configuration declares:

- `project_id`, explicit project root, output root;
- adapters and allowed roots;
- immutable Git ref policy;
- required `as_of` timestamp;
- freshness policy and privacy ceiling;
- source-authority ordering;
- rendering budget and hard resource caps;
- a project-local private `public_handle_salt`, supplied through a secret reference rather than emitted raw.

Unknown keys fail. Relative paths resolve against project root, never process cwd. Before hashing, paths become project-root-relative POSIX paths, keys are sorted, defaults are materialized, Unicode is NFC-normalized, and host absolute paths are excluded. The resolved salt digest, never the salt bytes, is included in the canonical config digest; salt rotation intentionally changes `snapshot_id` and all public handles.

Privacy levels are the total order `public < internal < confidential < restricted`. Merging takes the maximum, most restrictive source level. The compilation ceiling is an upper visibility allowance: a statement may render only when its merged privacy is at or below the selected ceiling. Unknown levels fail closed.

Authority rules may only rank existing adapter/source identifiers. Their schema has no `value`, object, or fact-generation field. An effective choice records `effective_by: {rule_id, config_digest}`. Every losing statement remains with `superseded_by`; incomparable authority claims produce a conflict rather than an implicit winner.

Authority is a declared total order for initial Preview. A source omitted from that order is incomparable and creates a conflict when it competes for the same claim. A statement's derived authority is no stronger than the strongest cited input, and merged privacy follows the total order above.

## 4. Statement, identity, time, and conflict semantics

Each observation has a source-local `observation_id`. Source identity never changes semantic IDs.

Canonical digest framing is normative: SHA-256 over UTF-8 NFC bytes; each field is encoded as a one-byte presence marker followed by an unsigned 64-bit big-endian byte length and bytes. Absent optional fields use the absent marker, distinct from an empty string. Lists and maps use canonical JSON with sorted keys, no insignificant whitespace, and the same framed UTF-8 representation. This prevents delimiter ambiguity.

- `claim_key = sha256(frame(subject, predicate, scope))`
- `statement_id = sha256(frame(claim_key, canonical object, valid_from, valid_to))`
- `sources[] = {observation_id, adapter, locator, content_digest, observed_revision, authority, privacy}`

Validity intervals are UTC half-open `[valid_from, valid_to)`. Missing `valid_from` is negative infinity; missing `valid_to` is positive infinity. Two intervals overlap iff each starts before the other's end, with infinity handled as above. Identical claim/object observations with overlapping or adjacent intervals merge to their interval union and ordered source set. A contradiction is the same `claim_key`, different canonical object, and overlapping validity intervals. Predicate-specific exceptions require a checked-in contradiction table; unknown predicate semantics fail closed rather than guessing. Non-overlapping intervals coexist.

`as_of` is required input. Core cannot call `time.time`, `datetime.now`, or equivalent. The strict RFC3339 subset accepts `YYYY-MM-DDTHH:MM:SS[.1-6 digits](Z|±HH:MM)`, rejects leap seconds and `-00:00`, and normalizes to UTC with `Z`; Python 3.9/3.12 must produce identical bytes.

- `semantic_digest` includes canonical statements, claim keys, objects, validity, source content digests, and authority decisions.
- It excludes `as_of`, freshness labels, duration, host paths, formatting, and ordering accidents.
- `snapshot_id` includes `semantic_digest`, normalized config digest, compiler schema, and `as_of`.
- Unchanged inputs plus the same `as_of` produce byte-identical files. Different `as_of` may change freshness/snapshot identity but not `semantic_digest`.

## 5. Adapters and source binding

Initial adapters are Git, Project Card, MemKraft, and State Contract.

- Git reads bytes and metadata from a configured commit tree, never the mutable working tree. Dirty state is a separate observation. Blob bytes are hashed without newline normalization; symlinks and modes are explicit.
- Project Card and MemKraft adapters enforce resolved path-component containment and output-root exclusion.
- State Contract accepts host-collected observations only. Each observation is bound by a content digest over canonical bytes; a missing/mismatched binding fails closed.
- Core never performs network collection.

Path-open race mitigation uses `O_NOFOLLOW` where available, then validates `fstat`/resolved containment after open. Preview documents residual TOCTOU limitations per platform. Phase 0 malicious-path fixtures only test normalization; real symlink escape coverage begins with adapter I/O in Phase 2.

## 6. Public-safe envelope

Public-safe is an envelope-level transform before any output is serialized, followed by a complete-output leak gate.

- Locators become project-relative paths or opaque handles.
- Private statements are omitted without exposing their IDs, digests, conflict links, or existence beyond aggregate counts.
- Low-entropy identifiers use a project-local private salt for externally visible handles.
- Conflict and supersession links are rewritten only among retained statements.
- Excerpts are length-bounded and privacy-checked.
- Secret-shaped fixture values, absolute paths, private IDs, and unbounded excerpts are scanned across every emitted byte; any match blocks publication.

Conflict pairs and supersession groups are atomic rendering units: include the full unit or omit it together. If omitted for budget, `context.md` contains a visible aggregate conflict banner while private IDs remain absent.

## 7. Resource and retention safety

Preview config requires hard ceilings for total input bytes, per-file bytes, JSONL line length, JSON nesting depth, observation/statement count, excerpt size, rendered bytes, and compile duration. Exceeding a limit fails with a structured diagnostic; it is not a performance warning.

Historical snapshots are derived copies, not erasure authorities. Retention is explicit by count and age. `clean --all` removes only marker-owned snapshots whose `project_id` and resolved output root match; an older compiler schema is diagnosed but does not make owned snapshots impossible to clean. A source tombstone/retraction marks affected statements retracted in new snapshots; previous snapshots remain until retention cleanup and are never described as erased from backups or external copies.

Compile-duration limits are enforced by the CLI/driver boundary with an injected monotonic clock. Pure core receives a decrementing budget/cancellation token and never reads wall or monotonic time itself.

## 8. Atomic snapshot protocol

1. Acquire a project/output lock using a platform adapter with explicit macOS/Linux and Windows behavior; Preview does not claim NFS lock safety.
2. Remove stale temp directories only while holding the lock and only with valid ownership markers.
3. Write a same-parent temp snapshot, fsync each file, fsync the temp directory.
4. If the content-addressed destination exists, verify its manifest/digests. If identical, discard temp; if different, fail closed.
5. Rename temp to destination and fsync the snapshots parent directory.
6. Under the same lock, reread `manifest.json`, update via temp file + fsync + atomic replace, then fsync its parent.
7. Release lock. Readers ignore temp paths and reject incomplete/unknown-schema snapshots.

Crash, duplicate-writer, stale-lock, existing-destination, and manifest-race fixtures are mandatory.

## 9. TDD implementation sequence

### Task 1: Freeze executable semantics

**Files:**
- Create: `docs/PROJECT_MEMORY_COMPILER.md`
- Create: `tests/fixtures/project_memory/*.json`
- Create: `tests/test_project_memory_contract.py`

**Steps:**
1. Write strict-schema tests for SHA-256 framed canonical IDs, `as_of`, digest field lists, validity intervals, authority rank-only rules, privacy order/ceiling, contradiction intervals, statement-kind-to-output-bucket mapping, schema rejection, salt/config binding, and resource caps.
2. Mark not-yet-implemented contract tests `xfail(strict=True)` so the contract can land without silently green behavior.
3. Run `pytest tests/test_project_memory_contract.py -q`; expected: only declared strict xfails, no unexpected pass/fail.
4. Commit contract and fixtures.

**Exit:** Bounded semantics are executable before implementation; no unresolved identity/time/conflict choice remains.

### Task 2: Implement pure model and reducer

**Files:**
- Create: `src/memkraft/project_memory/model.py`
- Create: `src/memkraft/project_memory/normalize.py`
- Create: `src/memkraft/project_memory/reducer.py`
- Create: `scripts/check_project_memory_compat.py`
- Modify: `.github/workflows/gym-gate.yml`
- Modify: `tests/test_project_memory_contract.py`

**Steps:**
1. Turn one identity/config test red, implement minimum, turn green, commit.
2. Repeat for strict RFC3339 parsing on real Python 3.9 and 3.12.
3. Repeat for merge, privacy meet, authority order, contradictions, and retractions.
4. Add permutation tests proving input order does not change semantic output.
5. Add static/runtime gates forbidding Python 3.10+ syntax and core clock calls.

**Exit:** Phase 1 has no xfails; semantic digest is order/format/as-of independent; conflicts never disappear.

### Task 3: Add read-only adapters

**Files:**
- Create: `src/memkraft/project_memory/adapters/{git,project_card,memkraft,state_contract}.py`
- Create: `tests/test_project_memory_adapters.py`

**Steps:**
1. TDD immutable Git tree reads and dirty-state separation.
2. TDD path containment, output exclusion, symlink escape, post-open validation.
3. TDD malformed JSONL, missing refs, digest binding, unknown schemas, privacy ceiling.
4. Verify no adapter writes or performs network access.

**Exit:** All source bytes are bound to receipts and no derived output can be re-ingested.

### Task 4: Implement owned atomic snapshots

**Files:**
- Create: `src/memkraft/project_memory/store.py`
- Create: `tests/test_project_memory_store.py`

**Steps:**
1. TDD ownership marker and marker-required cleanup.
2. TDD file/directory fsync and same-parent atomic rename.
3. TDD identical destination reuse vs digest mismatch failure.
4. TDD concurrent writers, manifest lock/update, kill recovery, stale-temp cleanup.
5. TDD unknown schema rejection and retention/retraction behavior.

**Exit:** No interrupted write appears complete and cleanup cannot escape an owned root.

### Task 5: Add bounded renderer and public-safe gate

**Files:**
- Create: `src/memkraft/project_memory/render.py`
- Create: `tests/test_project_memory_render.py`

**Steps:**
1. TDD conflict/supersession atomic budget units.
2. TDD public locator normalization and private-link removal.
3. TDD aggregate omission diagnostics without IDs.
4. Scan complete output against secret/path fixtures.
5. Enforce all hard resource caps.

**Exit:** Public-safe output contains no private handle/path/secret and budget truncation cannot hide one side of a conflict.

### Task 6: Add CLI and read-only API

**Files:**
- Create: `src/memkraft/project_memory/cli.py`
- Modify: MemKraft CLI registration files identified during implementation
- Create: `tests/test_project_memory_cli.py`

**Steps:**
1. Add `compile`, `inspect`, `check`, `clean` behind opt-in commands.
2. Fix exit codes: 0 healthy, 1 diagnosed unhealthy state, 2 usage/config error.
3. Define `inspect --json` as a versioned public contract.
4. Run from an unrelated cwd using a fresh wheel.

**Exit:** Existing callers/defaults are unchanged; invalid paths fail cleanly; markerless clean is refused.

### Task 7: Integrate and release Preview

**Files:**
- Modify: context compiler integration only through explicit project-memory path/budget
- Add: E2E fixture and release docs

**Steps:**
1. TDD opt-in-only integration and unchanged default output.
2. Replay at least three maintained projects with checked-in anonymized fixtures.
3. Define `zero silent conflict loss` as every source contradiction producing a retained conflict group or visible aggregate omission diagnostic.
4. Run full suite, Python 3.9/3.12 jobs, package build/twine, fresh-wheel CLI/API, security scans, and exact Opus review.
5. Resolve all blocker/important findings and rerun immutable review.

**Exit:** Preview ships only with zero silent conflict loss, reproducible semantic digests, and no authority/public-safe confusion.

## 10. Release gates

- Full MemKraft suite, conformance, packaging, and fresh wheel on Python 3.9/3.12.
- Determinism/permutation, malformed input, path escape, output ownership, interruption, concurrency, schema compatibility, retraction, resource-cap, and public-safe adversarial tests.
- Locked small/medium/large benchmark protocol and raw results. Performance claims wait for baselines, but hard safety caps block Preview immediately.
- Exact `claude-opus-5` review over a frozen diff with zero blocker/important.
- Never auto-enable generated context globally during Preview.

## 11. First bounded pull request

Only Tasks 1 and 2: normative contract, fixtures, pure model/normalizer/reducer, and tests. No adapters, filesystem writes, CLI, renderer, or integration. The slice is ready because identity, digest, time, authority, privacy, contradiction, resource, and schema semantics are fixed above.
