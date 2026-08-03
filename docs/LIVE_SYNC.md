# Local-first live sync

Markdown is canonical. Everything below is **derived, disposable state**
that can be deleted at any time and rebuilt from the Markdown alone.

No new dependencies: the core stays stdlib-only, `watch` still needs the
optional `watchdog` extra, and embeddings still need the optional
`embedding` extra. Default retrieval is unchanged (BM25 / `search_smart`).

## 1. Path-aware invalidation

`watch` used to react to a change by calling `mk.search('__watch_ping__')`
and hoping some cache noticed — and it did nothing at all on delete.

```python
mk.live_sync_apply(path, "create" | "modify" | "delete" | "move",
                   old_path=...,           # required for move
                   embeddings="auto",      # "auto" | True | False
                   provenance=True)
```

* Invalidates the exact path(s) through the canonical write-path hook
  (`_ReadCache.invalidate` → `_corpus_index.invalidate(path)`), so the
  next search can update the BM25 index for that one file instead of
  rebuilding the corpus.
* A **move invalidates both sides** — the old path no longer holds the
  document, the new one now does.
* Anything under `.memkraft/` is ignored: derived state never feeds back
  into itself.

## 2. Change-event envelope

Watcher-observed changes append one JSON line to
`.memkraft/live-sync/events.jsonl`:

```json
{"schema": "memkraft.live_sync.change_event/1",
 "event_id": "…", "operation": "move", "path": "/…/after.md",
 "old_path": "/…/before.md", "observed_at": "2026-08-03T10:11:12Z",
 "fingerprint": "sha256:…", "source": "watch"}
```

`fingerprint` is present only for files that still exist. Each envelope is
linked to a provenance record under its `event_id`
(`transform: "live_sync.<operation>"`, `kind: "change_event"`) — the
record references the file, it never inlines the file's contents.

Invalidation happens **before** the log write, so a full disk or a
read-only `.memkraft` degrades the audit trail without ever costing
retrieval correctness.

## 3. Freshness and repair

```python
mk.live_sync_freshness()      # canonical Markdown vs derived state
mk.live_sync_repair()         # rebuild derived state from Markdown
```

```
memkraft freshness [--path DIR] [--repair] [--json]
```

BM25 is in-memory only, so its reported states are the truthful ones:

| state       | meaning                                                     |
|-------------|-------------------------------------------------------------|
| `not_built` | no index has been built in this process (not "file missing") |
| `stale`     | corpus files are missing from / outdated in / orphaned in it |
| `fresh`     | index fingerprint matches the corpus exactly                 |

The embedding section reports `absent` / `corrupt` / `stale` / `fresh`
plus the specific missing, stale, orphaned and model-mismatched paths.
`absent` is not an error — embeddings are opt-in.

`live_sync_repair()` rebuilds BM25 from Markdown and, when an embedding
index already exists, re-encodes it with `build_embeddings(force=True)`.
Deleting `.memkraft/` entirely and calling repair is a supported recovery
path and is covered by a test.

## 4. Single-path embedding sync

```python
mk.embedding_sync_path(path, "create" | "modify" | "delete" | "move",
                       old_path=...)
```

Encodes only the changed non-empty Markdown file, drops stale vectors
(deletes, the source side of a move, files that became empty), preserves
every unrelated record, and rewrites `index.jsonl` atomically via
tmp + `os.replace`.

Strictly opt-in. `live_sync_apply(embeddings="auto")` — what `watch`
uses — syncs embeddings **only if `index.jsonl` already exists**, checked
by a plain path probe, so running `watch` never installs or loads the
optional model.

## 5. Benchmark

```
python benchmarks/live_sync_bench.py --docs 200 --json out.json
```

Reports, for a one-file change versus a full rebuild:

* `bm25.{full_rebuild,incremental}.files_read`
* `embedding.{full_rebuild,incremental}.files_encoded`
* `wall_ms` for each

Structural counts are the headline: they are exact and deterministic
(N files read/encoded vs 1). `wall_ms` is reported but indicative only,
and the embedding half runs against a deterministic fake encoder, so it
excludes real inference cost. Both paths still `stat()` every corpus file
to fingerprint it — the saving is in reads and encodes, not stats. A
smaller output payload is never counted as a speedup.
