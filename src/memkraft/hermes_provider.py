"""Hermes Agent memory-provider adapter for MemKraft.

This module is loaded lazily through the ``hermes_agent.memory_providers``
entry-point group. Importing :mod:`memkraft` itself therefore remains
independent of Hermes Agent.
"""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

from . import MemKraft


class MemKraftMemoryProvider(MemoryProvider):
    """Expose MemKraft as a context-only Hermes memory provider."""

    def __init__(self) -> None:
        self._store: Optional[MemKraft] = None
        self._session_id = ""

    @property
    def name(self) -> str:
        return "memkraft"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
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

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if self._store is None:
            return
        content = "User: {}\nAssistant: {}".format(user_content, assistant_content).strip()
        if not content:
            return
        source_session = session_id or self._session_id or "unknown"
        source = "hermes:{}".format(source_session)
        with redirect_stdout(io.StringIO()):
            extracted = self._store.extract(content, source=source)
            if not extracted:
                # MemKraft's regex extractor intentionally ignores prose that
                # does not match a structured entity/fact pattern. Hermes'
                # sync_turn contract still requires a completed turn to be
                # persisted, so retain otherwise-unclassified conversation
                # text in one bounded session note instead of silently losing
                # it. Search indexes live notes, making it available to the
                # next turn's prefetch immediately.
                note_name = "Hermes session {}".format(source_session)
                self._store.track(note_name, entity_type="session", source=source)
                self._store.update(note_name, content, source=source)

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
