"""Hermes Agent memory-provider adapter for MemKraft.

This module is loaded lazily through the ``hermes_agent.memory_providers``
entry-point group. Importing :mod:`memkraft` itself therefore remains
independent of Hermes Agent.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import threading
import time
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

from . import MemKraft
from .install_integrity import installation_report


_TURN_WRITE_LOCK = threading.RLock()
_DEFAULT_TURN_FILE_BYTES = 256 * 1024
_MIN_TURN_FILE_BYTES = 512


def _turn_file_limit() -> int:
    try:
        configured = int(os.environ.get(
            "MEMKRAFT_HERMES_TURN_FILE_BYTES", str(_DEFAULT_TURN_FILE_BYTES)
        ))
    except ValueError:
        configured = _DEFAULT_TURN_FILE_BYTES
    return max(_MIN_TURN_FILE_BYTES, configured)


def _utf8_prefix(payload: bytes, byte_limit: int) -> tuple[bytes, bytes]:
    """Split one UTF-8-safe prefix, or return empty when no code point fits."""
    end = min(len(payload), byte_limit)
    while end:
        try:
            payload[:end].decode("utf-8")
            return payload[:end], payload[end:]
        except UnicodeDecodeError:
            end -= 1
    return b"", payload


@contextmanager
def _process_lock(path: Path):
    """Serialize session-note updates between processes sharing one store."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+b", closefd=True) as handle:
        if os.name == "nt":
            import msvcrt

            if os.fstat(handle.fileno()).st_size == 0:
                os.write(handle.fileno(), b"0")
                os.fsync(handle.fileno())
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class MemKraftMemoryProvider(MemoryProvider):
    """Expose MemKraft as a context-only Hermes memory provider."""

    def __init__(self) -> None:
        self._store: Optional[MemKraft] = None
        self._session_id = ""
        self._installation_report: Optional[Dict[str, object]] = None

    @property
    def name(self) -> str:
        return "memkraft"

    def is_available(self) -> bool:
        return True

    def installation_report(self) -> Dict[str, object]:
        """Return a fresh, machine-readable report for this interpreter."""
        return installation_report()

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        mode = os.environ.get("MEMKRAFT_INSTALL_CHECK", "warn").strip().lower()
        if mode != "off":
            try:
                self._installation_report = self.installation_report()
            except Exception as exc:
                self._installation_report = {
                    "consistent": False,
                    "reasons": ["probe_exception"],
                    "errors": ["{}: {}".format(type(exc).__name__, exc)],
                }
            if not self._installation_report["consistent"]:
                reasons = self._installation_report.get("reasons")
                detail = ", ".join(str(reason) for reason in reasons) if isinstance(reasons, list) else "unknown drift"
                if mode == "strict":
                    raise RuntimeError("Inconsistent MemKraft installation: {}".format(detail))
                logging.getLogger(__name__).warning(
                    "Inconsistent MemKraft installation: %s; run `memkraft selfupdate --converge`",
                    detail,
                )
        hermes_home = Path(str(kwargs.get("hermes_home") or Path.home() / ".hermes"))
        base_dir = Path(os.environ.get("MEMKRAFT_DIR", str(hermes_home / "memkraft")))
        self._store = MemKraft(base_dir=str(base_dir))
        with redirect_stdout(io.StringIO()):
            self._store.init(verbose=False)
        self._session_id = session_id

    def system_prompt_block(self) -> str:
        return (
            "MemKraft provides persistent local memory. Recalled entries are "
            "reference context; prefer the user's current message on conflicts."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._store is None or not query.strip():
            return ""
        with redirect_stdout(io.StringIO()):
            results = self._store.search(query, top_k=5)
        if not results:
            return ""
        lines = ["MemKraft recall:"]
        for result in results:
            snippet = str(result.get("snippet") or result.get("match") or "").strip()
            source = str(result.get("file") or "memory")
            if snippet:
                lines.append("- {}: {}".format(source, snippet))
        return "\n".join(lines) if len(lines) > 1 else ""

    def _persist_completed_turn(self, session_id: str, content: str) -> None:
        """Append a completed Hermes turn to bounded, searchable chunks."""
        if self._store is None:
            return
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
        prefix = "hermes-session-{}".format(digest)
        directory = self._store.live_notes_dir
        directory.mkdir(parents=True, exist_ok=True)
        limit = _turn_file_limit()
        header_template = "# Hermes session {} chunk {{}}\n\n".format(digest)
        entry = "## Completed turn\n\n{}\n\n".format(content)

        changed: List[Path] = []
        lock_path = directory / ".{}.lock".format(prefix)
        with _TURN_WRITE_LOCK, _process_lock(lock_path):
            existing = sorted(directory.glob(prefix + "-*.md"))
            index = int(existing[-1].stem.rsplit("-", 1)[-1]) if existing else 1
            remaining = entry.encode("utf-8")
            while remaining:
                path = directory / "{}-{:06d}.md".format(prefix, index)
                header = header_template.format(index).encode("utf-8")
                current_size = path.stat().st_size if path.exists() else len(header)
                available = limit - current_size
                if available <= 0:
                    index += 1
                    continue
                fragment, remaining = _utf8_prefix(remaining, available)
                if not path.exists():
                    path.write_bytes(header)
                with path.open("ab") as handle:
                    handle.write(fragment)
                    handle.flush()
                    os.fsync(handle.fileno())
                changed.append(path)
                if remaining:
                    index += 1

        from ._corpus_index import invalidate as invalidate_corpus
        from ._read_cache import get_cache
        for path in changed:
            get_cache().invalidate(path)
            invalidate_corpus(path)
        bump_generation = getattr(self._store, "_bump_cache_generation", None)
        if callable(bump_generation):
            bump_generation()

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        if self._store is None or not (user_content or assistant_content):
            return
        content = "User: {}\nAssistant: {}".format(user_content, assistant_content)
        source_session = session_id or self._session_id or "unknown"
        source = "hermes:{}".format(source_session)
        self._persist_completed_turn(source_session, content)
        artifact_provenance: Dict[str, Any] = {}
        artifact_provenance.update(metadata or {})
        artifact_provenance.update(provenance or {})
        artifact_provenance.update(kwargs)
        artifact_provenance["session_id"] = source_session
        if messages:
            for message in messages:
                role = message.get("role")
                message_id = message.get("message_id", message.get("id"))
                if message_id is not None and role in ("user", "assistant"):
                    artifact_provenance.setdefault(role + "_platform_message_id", message_id)
        self._store.persist_artifact(content, provenance=artifact_provenance, source=source)
        with redirect_stdout(io.StringIO()):
            if messages is None:
                # Preserve the pre-3.6 extraction contract for existing callers.
                self._store.extract(content, source=source)
            else:
                # Role-aware callers retain separate evidence boundaries.
                if user_content:
                    self._store.extract(user_content, source=source + "#user")
                if assistant_content:
                    self._store.extract(assistant_content, source=source + "#assistant")

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs: Any) -> str:
        return json.dumps({"success": False, "error": "MemKraft exposes no model tools"})

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs: Any,
    ) -> None:
        self._session_id = new_session_id

    def backup_paths(self) -> List[str]:
        configured = os.environ.get("MEMKRAFT_DIR")
        return [str(Path(configured).expanduser())] if configured else []


def register(ctx: Any) -> None:
    """Register the provider with Hermes' plugin collector."""
    ctx.register_memory_provider(MemKraftMemoryProvider())


__all__ = ["MemKraftMemoryProvider", "register"]
