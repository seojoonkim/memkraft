"""memkraft watch — filesystem watcher that auto-reindexes memory/ on change.

Requires the `watchdog` extra: `pip install "memkraft[watch]"`.

v3.2: every observed change is applied through
:meth:`MemKraft.live_sync_apply`, which invalidates the *specific* paths
involved (both sides of a move) and appends a change-event envelope.
The old behaviour — a dummy ``search('__watch_ping__')`` and no handling
of deletes at all — is gone.

Embeddings are touched only when an embedding index already exists on
disk, so running ``watch`` never installs or loads the optional model.

Prints events:
    [index] <path>        — file created/modified, invalidated + reindexed
    [remove] <path>       — file deleted, dropped from derived state
    [rename] <old> → <new>
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

from .core import MemKraft


_WATCHDOG_HINT = (
    "watchdog is not installed. install it with:\n"
    "    pip install 'memkraft[watch]'\n"
    "or add watchdog to your environment directly."
)


def _try_import_watchdog():
    try:
        from watchdog.events import FileSystemEventHandler  # noqa: F401
        from watchdog.observers import Observer  # noqa: F401
        return True
    except ImportError:
        return False


def _is_md(path: str) -> bool:
    return str(path).endswith(".md")


def _is_derived(path: str) -> bool:
    """Never react to our own derived state under ``.memkraft``."""
    return ".memkraft" in Path(str(path)).parts


def _build_handler(mk: MemKraft):
    from watchdog.events import FileSystemEventHandler

    class _MKHandler(FileSystemEventHandler):
        def _apply(self, path: str, operation: str, old_path: Optional[str] = None) -> None:
            try:
                mk.live_sync_apply(path, operation, old_path=old_path)
            except Exception as e:
                print(f"  [warn] live sync failed for {path}: {e}", flush=True)

        def on_created(self, event):
            if event.is_directory or not _is_md(event.src_path) or _is_derived(event.src_path):
                return
            print(f"  [index] {event.src_path}", flush=True)
            self._apply(event.src_path, "create")

        def on_modified(self, event):
            if event.is_directory or not _is_md(event.src_path) or _is_derived(event.src_path):
                return
            print(f"  [index] {event.src_path}", flush=True)
            self._apply(event.src_path, "modify")

        def on_deleted(self, event):
            if event.is_directory or not _is_md(event.src_path) or _is_derived(event.src_path):
                return
            print(f"  [remove] {event.src_path}", flush=True)
            self._apply(event.src_path, "delete")

        def on_moved(self, event):
            if event.is_directory:
                return
            if _is_derived(event.src_path) and _is_derived(event.dest_path):
                return
            print(f"  [rename] {event.src_path} → {event.dest_path}", flush=True)
            # Both sides matter: the old path no longer holds the doc,
            # the new one now does.  live_sync_apply invalidates both
            # and ignores whichever side isn't canonical markdown.
            self._apply(event.dest_path, "move", old_path=event.src_path)

    return _MKHandler()


def _resolve_target(path: str) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    return Path(MemKraft().base_dir).expanduser().resolve()


def run(path: str = "", once: bool = False) -> int:
    if not _try_import_watchdog():
        print(f"❌ {_WATCHDOG_HINT}")
        return 2

    target = _resolve_target(path)
    if not target.exists():
        print(f"❌ watch target does not exist: {target}")
        print(f"   run `memkraft init` first.")
        return 1

    # The watched target may differ from the default MEMKRAFT_DIR — root
    # the MemKraft instance at what we actually watch, otherwise we'd
    # invalidate paths in an unrelated corpus.
    mk = MemKraft(base_dir=str(target))

    # Defer-import observer so tests can stub _try_import_watchdog without
    # needing the real dependency installed.
    try:
        from watchdog.observers import Observer
    except ImportError:
        print(f"❌ {_WATCHDOG_HINT}")
        return 2

    print(f"👀 MemKraft watch: {target}")
    print("   press Ctrl+C to stop")

    handler = _build_handler(mk)
    observer = Observer()
    observer.schedule(handler, str(target), recursive=True)
    observer.start()

    try:
        if once:
            # debug mode: exit quickly after a short tick so tests don't hang
            time.sleep(0.1)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n  stopping watcher…")
    finally:
        observer.stop()
        observer.join(timeout=2)

    return 0


def cmd(args) -> int:
    return run(path=getattr(args, "path", ""), once=getattr(args, "once", False))
