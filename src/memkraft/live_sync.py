"""v3.2 — local-first live sync: explicit invalidation, change events, freshness.

Markdown stays canonical.  Everything this module maintains — the
in-memory BM25 corpus index, the optional embedding JSONL, and the
append-only change-event log — is **derived, disposable state** that can
be deleted at any moment and rebuilt from the markdown alone
(:meth:`LiveSyncMixin.live_sync_repair`).

Three small pieces:

``live_sync_apply(path, operation, old_path=None)``
    Path-aware invalidation for ``create`` / ``modify`` / ``delete`` /
    ``move``.  This replaces the old ``watch`` behaviour of firing a
    dummy ``search('__watch_ping__')`` and hoping a cache noticed.

``live_sync_events()``
    Reads back the append-only envelope written under
    ``.memkraft/live-sync/events.jsonl``.  Each envelope records the
    operation, path, ``observed_at``, a content fingerprint (for files
    that still exist) and ``old_path`` for moves, and is linked to a
    provenance record by ``event_id``.  Log I/O failures are always
    non-fatal: invalidation happens first, the envelope second.

``live_sync_freshness()`` / ``live_sync_repair()``
    Diagnostics comparing canonical markdown against derived state, and
    a repair that rebuilds derived state from markdown.  BM25 is
    in-memory only, so its honest states are ``not_built`` / ``stale`` /
    ``fresh`` — never "file missing".

Stdlib only.  No new mandatory dependencies.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "LiveSyncMixin",
    "CHANGE_EVENT_SCHEMA",
    "VALID_OPERATIONS",
    "cmd",
]

CHANGE_EVENT_SCHEMA = "memkraft.live_sync.change_event/1"
VALID_OPERATIONS = ("create", "modify", "delete", "move")

_DERIVED_DIRNAME = ".memkraft"
_EVENT_LOG_BASENAME = "events.jsonl"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fingerprint(path: Path) -> Optional[str]:
    """Content fingerprint for an existing file, else ``None``."""
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return "sha256:" + h.hexdigest()


def _is_derived_path(path: Path) -> bool:
    """True for anything under ``.memkraft`` — derived, never canonical."""
    return _DERIVED_DIRNAME in path.parts


class LiveSyncMixin:
    """Explicit, path-aware synchronisation of MemKraft's derived state."""

    # ── paths ────────────────────────────────────────────────────
    def _live_sync_dir(self) -> Path:
        return Path(getattr(self, "base_dir", Path.cwd())) / _DERIVED_DIRNAME / "live-sync"

    def _live_sync_event_log_path(self) -> Path:
        return self._live_sync_dir() / _EVENT_LOG_BASENAME

    def _live_sync_abs(self, path) -> Optional[Path]:
        if path is None:
            return None
        p = Path(str(path)).expanduser()
        if not p.is_absolute():
            base = Path(getattr(self, "base_dir", Path.cwd())).expanduser().resolve()
            # Watchdog normally emits a path relative to cwd, while direct API
            # callers commonly pass a path relative to base_dir. Prefer an
            # existing cwd-relative path to avoid joining base_dir twice.
            cwd_relative = p.resolve()
            p = cwd_relative if cwd_relative.exists() else base / p
        return p.resolve(strict=False)

    # ── invalidation ─────────────────────────────────────────────
    def _live_sync_invalidate(self, path: Path) -> None:
        """Drop ``path`` from the read cache and the BM25 corpus index.

        ``_ReadCache.invalidate`` is the canonical write-path hook: it
        evicts the cached text *and* forwards to
        ``_corpus_index.invalidate(path)``, which records the path as a
        pending single-file invalidation so the next search can update
        incrementally instead of rebuilding the whole corpus.
        """
        try:
            from ._read_cache import get_cache

            get_cache().invalidate(path)
        except Exception:
            # Fall back to the corpus hook directly — invalidation must
            # not depend on the read cache being healthy.
            try:
                from . import _corpus_index

                _corpus_index.invalidate(path)
            except Exception:
                pass

    # ── public: apply one observed change ────────────────────────
    def live_sync_apply(
        self,
        path,
        operation: str,
        old_path=None,
        *,
        embeddings: Any = "auto",
        provenance: bool = True,
        source: str = "watch",
    ) -> dict:
        """Apply one observed filesystem change to derived state.

        Parameters
        ----------
        path:
            The markdown file the change landed on (the *destination*
            for a move).
        operation:
            ``create`` | ``modify`` | ``delete`` | ``move``.
        old_path:
            Required for ``move`` — the path the file came from.  Both
            sides are invalidated.
        embeddings:
            ``"auto"`` (default) syncs the embedding index **only if one
            already exists on disk**, so merely running ``watch`` never
            installs or loads the optional model.  ``True`` forces the
            sync, ``False`` skips it.
        provenance:
            Link the emitted change event to a provenance record.

        Returns a stats dict.  Invalidation happens before any I/O that
        can fail, so a broken event log never costs correctness.
        """
        operation = str(operation or "").strip().lower()
        if operation not in VALID_OPERATIONS:
            raise ValueError(
                f"unknown operation: {operation!r} (expected one of {VALID_OPERATIONS})"
            )

        target = self._live_sync_abs(path)
        source_path = self._live_sync_abs(old_path)
        if operation == "move" and source_path is None:
            raise ValueError("move requires old_path")

        # Which paths this change makes stale.  A move makes *both*
        # sides stale: the old path no longer holds the document, the
        # new one now does.
        candidates: List[Path] = []
        if operation == "move":
            candidates = [source_path, target]
        elif target is not None:
            candidates = [target]

        touched: List[Path] = []
        for p in candidates:
            if p is None:
                continue
            if p.suffix.lower() != ".md":
                continue
            if _is_derived_path(p):
                # Never recurse into our own derived state.
                continue
            touched.append(p)

        result: Dict[str, Any] = {
            "operation": operation,
            "path": str(target) if target is not None else "",
            "old_path": str(source_path) if source_path is not None else "",
            "invalidated": [],
            "event": None,
            "event_error": None,
            "embedding": None,
            "skipped": not touched,
        }
        if not touched:
            return result

        # 1. Invalidation first — this is the correctness-critical step.
        for p in touched:
            self._live_sync_invalidate(p)
            result["invalidated"].append(str(p))

        # 2. Change-event envelope (best effort).
        event = self._live_sync_build_event(operation, target, source_path, source)
        try:
            self._live_sync_append_event(event)
            result["event"] = event
        except OSError as exc:
            result["event_error"] = f"{type(exc).__name__}: {exc}"

        # 3. Provenance linkage (best effort, and deliberately tiny —
        #    the envelope references the file, it never inlines it).
        if provenance and result["event"] is not None:
            try:
                self.provenance_record(
                    event["event_id"],
                    [{"file": event["path"]}],
                    transform=f"live_sync.{operation}",
                    kind="change_event",
                    value=event.get("fingerprint", "") or "",
                )
                result["provenance_record_id"] = event["event_id"]
            except Exception as exc:  # provenance is diagnostic, not critical
                result["provenance_error"] = f"{type(exc).__name__}: {exc}"

        # 4. Optional embedding sync.
        if self._live_sync_should_sync_embeddings(embeddings):
            try:
                result["embedding"] = self.embedding_sync_path(
                    target, operation, old_path=source_path
                )
            except Exception as exc:
                result["embedding_error"] = f"{type(exc).__name__}: {exc}"

        return result

    def _live_sync_should_sync_embeddings(self, embeddings: Any) -> bool:
        if embeddings is True:
            return True
        if embeddings in (False, None):
            return False
        # "auto": only when an index already exists.  Checked by plain
        # path probe so we never import or load the optional model.
        index_path = (
            Path(getattr(self, "base_dir", Path.cwd()))
            / _DERIVED_DIRNAME
            / "embeddings"
            / "index.jsonl"
        )
        return index_path.exists()

    # ── change events ────────────────────────────────────────────
    def _live_sync_build_event(
        self,
        operation: str,
        target: Optional[Path],
        source_path: Optional[Path],
        source: str,
    ) -> dict:
        event: Dict[str, Any] = {
            "schema": CHANGE_EVENT_SCHEMA,
            "event_id": uuid.uuid4().hex,
            "operation": operation,
            "path": str(target) if target is not None else "",
            "observed_at": _utc_now_iso(),
            "source": source,
        }
        if operation == "move" and source_path is not None:
            event["old_path"] = str(source_path)
        if target is not None:
            fp = _fingerprint(target)
            if fp is not None:
                event["fingerprint"] = fp
        return event

    def _live_sync_append_event(self, event: dict) -> None:
        path = self._live_sync_event_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def live_sync_events(self, limit: Optional[int] = None) -> List[dict]:
        """Read back the append-only change-event log (oldest first)."""
        path = self._live_sync_event_log_path()
        out: List[dict] = []
        if not path.exists():
            return out
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except OSError:
            return out
        if limit is not None and limit >= 0:
            return out[-limit:] if limit else []
        return out

    def _live_sync_event_count(self) -> int:
        path = self._live_sync_event_log_path()
        try:
            with path.open("rb") as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0

    # ── freshness diagnostics ────────────────────────────────────
    def live_sync_freshness(self) -> dict:
        """Compare canonical markdown against derived state.

        BM25 lives in memory only, so ``not_built`` is a truthful state,
        not an error.  The embedding section is reported only as far as
        the optional index actually exists on disk.
        """
        from . import _corpus_index

        files = sorted(self._all_md_files(), key=str)
        _, items = _corpus_index._compute_fingerprint(files)
        current = {str(p): (s["mtime_ns"], s["size"]) for p, s in items}

        snap = _corpus_index.snapshot()
        if snap is None:
            bm25: Dict[str, Any] = {
                "state": "not_built",
                "note": "BM25 index is in-memory only and has not been built in this process",
                "corpus_files": len(current),
                "indexed_files": 0,
                "missing": [],
                "stale": [],
                "orphaned": [],
            }
        else:
            indexed = {str(p): tuple(v) for p, v in snap["doc_stat"].items()}
            missing = sorted(p for p in current if p not in indexed)
            orphaned = sorted(p for p in indexed if p not in current)
            stale = sorted(
                p for p in current if p in indexed and indexed[p] != current[p]
            )
            fresh = not (missing or stale or orphaned)
            bm25 = {
                "state": "fresh" if fresh else "stale",
                "corpus_files": len(current),
                "indexed_files": len(indexed),
                "missing": missing,
                "stale": stale,
                "orphaned": orphaned,
            }

        embedding: Dict[str, Any]
        try:
            embedding = self.embedding_index_state(files)
        except Exception as exc:
            embedding = {"state": "unknown", "error": f"{type(exc).__name__}: {exc}"}

        consistent = bm25["state"] == "fresh" and embedding.get("state") in (
            "fresh",
            "absent",
        )
        return {
            "base_dir": str(getattr(self, "base_dir", "")),
            "canonical_files": len(current),
            "bm25": bm25,
            "embedding": embedding,
            "events": self._live_sync_event_count(),
            "consistent": consistent,
        }

    def live_sync_repair(self, *, embeddings: Any = "auto") -> dict:
        """Rebuild derived state from canonical Markdown.

        Safe to call after deleting or corrupting any derived artefact —
        markdown alone is sufficient for full recovery.
        """
        from . import _corpus_index

        _corpus_index.reset_for_tests()
        index = _corpus_index.get_corpus_index(
            lambda: self._all_md_files(),
            self._search_tokens,
            self._safe_read,
        )
        out: Dict[str, Any] = {
            "bm25": {"rebuilt": True, "doc_count": index.doc_count},
            "embedding": None,
        }
        if self._live_sync_should_sync_embeddings(embeddings):
            try:
                out["embedding"] = self.build_embeddings(force=True)
            except Exception as exc:
                out["embedding_error"] = f"{type(exc).__name__}: {exc}"
        out["freshness"] = self.live_sync_freshness()
        return out


# ── CLI ──────────────────────────────────────────────────────────
def cmd(args) -> int:
    """`memkraft freshness [--repair] [--json]`."""
    from .core import MemKraft

    mk = MemKraft(base_dir=getattr(args, "path", "") or None)
    if getattr(args, "repair", False):
        report = mk.live_sync_repair()
        freshness = report["freshness"]
    else:
        report = None
        freshness = mk.live_sync_freshness()

    if getattr(args, "json", False):
        print(json.dumps(report if report is not None else freshness,
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    bm25 = freshness["bm25"]
    emb = freshness["embedding"]
    print(f"📂 canonical markdown: {freshness['canonical_files']} file(s)")
    print(f"   BM25 (in-memory):   {bm25['state']}")
    if bm25.get("missing") or bm25.get("stale") or bm25.get("orphaned"):
        print(
            f"      missing={len(bm25['missing'])} "
            f"stale={len(bm25['stale'])} orphaned={len(bm25['orphaned'])}"
        )
    print(f"   embeddings:         {emb.get('state')} ({emb.get('records', 0)} record(s))")
    print(f"   change events:      {freshness['events']}")
    if report is not None:
        print("🔧 repaired from Markdown")
    elif not freshness["consistent"]:
        print("   run `memkraft freshness --repair` to rebuild derived state from Markdown")
    return 0
