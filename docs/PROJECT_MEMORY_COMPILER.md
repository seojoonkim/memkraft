# Project Memory Compiler v0 Preview

> **Status:** Project Memory Compiler v0 Preview. The GA promotion, revision, or withdrawal decision deadline is **2027-02-14**.

The Project Memory Compiler (PMC) is an opt-in Python Preview that compiles an explicit project root's Markdown into deterministic, provenance-preserving snapshots. It does not assert facts, mutate project files, or integrate with `compile_context` and its frozen `usage_id` contract.

## Python API

```python
from memkraft import MemKraft

mk = MemKraft(base_dir="/tmp/memory")
plan = mk.project_build("/path/to/project", as_of="2026-08-15T00:00:00Z")
built = mk.project_build("/path/to/project", as_of="2026-08-15T00:00:00Z", dry_run=False)
status = mk.project_status("/path/to/project")
updated = mk.project_update("/path/to/project", as_of="2026-08-16T00:00:00Z", dry_run=False)
context = mk.project_context("release lineage", "/path/to/project", budget=1500)
```

`project_build` and `project_update` require an explicit RFC3339 `as_of`; core code never reads a clock. Builds are dry-run by default. `project_context` reads an existing snapshot and never builds implicitly.

## Input and output boundaries

- Input is UTF-8 Markdown under the explicit project root.
- `.memkraft/`, `AGENTS.md`, and `CLAUDE.md` are not inputs.
- A symlink resolving outside the root fails closed.
- Applied state is owned under `<base_dir>/.memkraft/project-memory/<project_id>/`.
- Raw excerpts live only in `evidence.jsonl`; `sections.jsonl` contains derived records citing `observation_id`.
- Owner directories use mode `0700`; files use `0600`.
- Snapshots are atomic, append-only in v0, disposable, and rebuildable.

Identity uses NFC, presence-and-length framed SHA-256. `semantic_digest` excludes host paths and `as_of`; `snapshot_id` includes the semantic/config digests, compiler schema, and explicit `as_of`. Freshness uses size and mtime as a cheap probe but confirms changes with a content digest.

## Scope and limitations

PMC v0 performs deterministic section extraction only. It makes no claim/statement reduction, contradiction, authority, privacy-lattice, public-safe filtering, Git-object, State Contract, Project Card, retention, CLI, MCP, scheduler, watcher, embedding, LLM, or UI claim. Zero silent conflict loss holds only because v0 asserts no facts.

Windows is unsupported because the atomic durability protocol requires directory `fsync`. Containment uses resolved paths but does not yet use `O_NOFOLLOW`, leaving a documented residual TOCTOU window. Use only on a trusted local filesystem.

## Rollback

Stop writers, reinstall the prior MemKraft version, and ignore or manually delete `.memkraft/project-memory/`. The directory contains derived data only; no migration or schema rewrite is required.
