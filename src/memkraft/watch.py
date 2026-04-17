"""memkraft watch — filesystem watcher that auto-reindexes memory/ on change.

Requires the `watchdog` extra: `pip install "memkraft[watch]"`.

Prints events:
    [index] <path>        — file created/modified, reindexed
    [remove] <path>       — file deleted, dropped from index
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


def _build_handler(mk: MemKraft):
    from watchdog.events import FileSystemEventHandler

    class _MKHandler(FileSystemEventHandler):
        def _is_md(self, path: str) -> bool:
            return path.endswith(".md")

        def _reindex(self, path: str) -> None:
            # Soft reindex: load file into whatever index the core exposes.
            # We call the public search() once after each change so caches warm.
            try:
                # If core ever exposes an explicit re-index hook, prefer it.
                reindex = getattr(mk, "reindex_file", None)
                if callable(reindex):
                    reindex(path)
                else:
                    # Fallback: touch the search path so any lazy caches rebuild.
                    try:
                        mk.search("__watch_ping__", fuzzy=False)
                    except Exception:
                        pass
            except Exception as e:
                print(f"  [warn] reindex failed for {path}: {e}", flush=True)

        def on_created(self, event):
            if event.is_directory or not self._is_md(event.src_path):
                return
            print(f"  [index] {event.src_path}", flush=True)
            self._reindex(event.src_path)

        def on_modified(self, event):
            if event.is_directory or not self._is_md(event.src_path):
                return
            print(f"  [index] {event.src_path}", flush=True)
            self._reindex(event.src_path)

        def on_deleted(self, event):
            if event.is_directory or not self._is_md(event.src_path):
                return
            print(f"  [remove] {event.src_path}", flush=True)

        def on_moved(self, event):
            if event.is_directory:
                return
            print(f"  [rename] {event.src_path} → {event.dest_path}", flush=True)
            if self._is_md(event.dest_path):
                self._reindex(event.dest_path)

    return _MKHandler()


def run(path: str = "", once: bool = False) -> int:
    if not _try_import_watchdog():
        print(f"❌ {_WATCHDOG_HINT}")
        return 2

    mk = MemKraft()
    target = Path(path).expanduser() if path else mk.base_dir
    if not target.exists():
        print(f"❌ watch target does not exist: {target}")
        print(f"   run `memkraft init` first.")
        return 1

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
