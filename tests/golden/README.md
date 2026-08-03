# Slice-0 baseline verification (MKEP/0, plan §2.3 / §19.2)

Verified on branch `feat/mkep-3.3` at HEAD `451ed1b`, whose parent chain includes
the authoritative implementation baseline `b9453da` (tag `v3.2.0`).

## Release metadata

| Source | Value |
|---|---|
| `pyproject.toml:7` | `3.2.0` |
| `src/memkraft/__init__.py:3` | `3.2.0` |
| `CHANGELOG.md` first heading | `3.2.0` |

All three agree and are strictly less than the `3.3.0` target, so the plan's
version pin stands. No re-pin to a later 3.x minor is needed.

## Baseline suite

`python -m pytest -q` → **2299 passed, 2 skipped** before any Slice-0 file was
added. Slice 2 must reproduce this pass count for the pre-existing tests.

## Assumptions A1–A4

| ID | Claim | Result | Evidence |
|---|---|---|---|
| A1 | `DerivedViewsMixin._governance_lock()` wraps `store_core._lock_current_inode` | **confirmed** | `derived_views.py:72-74` calls `_lock_current_inode(str(p), os.O_RDWR\|os.O_CREAT)`, imported from `store_core` |
| A2 | `_append_audit` suppresses a duplicate `operation_id` | **confirmed** | `derived_views.py:97-100` returns `None` when any existing audit row shares the `operation_id` |
| A3 | `compile_context` derives `usage_id` as sha256 over a fixed identity dict | **confirmed** | `context_compiler.py:137-146`; identity keys are exactly `task, budget, objective, session_id, sections, sources`, serialized with `sort_keys=True, separators=(",", ":")`, and record `id` is stripped from every section item |
| A4 | No `execution*` module exists under `src/memkraft/` | **confirmed** | `src/memkraft/execution*.py` is empty |

A3 was checked first, as the plan requires. It holds, so §12.4 and gate G10
stand as written and no re-derivation is needed.

**A4 nuance.** The plan's command is `ls src/memkraft | grep -i execution`, which
matches the unrelated `reasoning_execution.py` and so appears to fail. The
assumption as stated ("no module named `execution*`") is true: nothing is
`execution`-prefixed. `test_execution_baseline.py` encodes the precise form.

## Golden `usage_id`

`tools/pin_golden_usage_id.py` builds a fully deterministic base — three fixed
events with explicit `valid_from`, a fixed task/budget/objective, no
`session_id` — and pins the resulting hash in `usage_id.json`:

```
dee4ddf2daf2b4d062cd0a892dd2143d2787df9c090816895244a1064cbe2788
```

`sections` are stored in identity shape (record `id` removed), because the store
mints a fresh uuid4 `id` per append and the hash never sees it.

Gate G10 (Slice 11) requires this value to be unchanged when
`compile_context` gains `goal_id` and `execution_budget` and produces no
`execution` section. **A change to this hash forbids release.**
