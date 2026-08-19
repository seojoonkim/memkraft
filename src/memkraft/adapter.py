"""Transport-neutral memory adapter contract for external agents.

The adapter is intentionally thin: MemKraft owns persistence and validation,
while Hermes, OpenClaw, MCP, and other hosts can expose the same four calls.
It does not evaluate, promote, activate, or authorize improvements.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class MemoryAdapter:
    """Stable ``remember``/``recall``/``feedback``/``health`` facade."""

    def __init__(self, memory: Any):
        self.memory = memory

    @staticmethod
    def _ok(operation: str, **payload: Any) -> Dict[str, Any]:
        return {"ok": True, "operation": operation, **payload}

    @staticmethod
    def _error(operation: str, error: Exception) -> Dict[str, Any]:
        code = getattr(error, "code", "E_INTERNAL")
        retryable = bool(getattr(error, "retryable", False))
        details = dict(getattr(error, "details", {}) or {})
        return {
            "ok": False,
            "operation": operation,
            "error": {
                "code": code,
                "message": str(error),
                "retryable": retryable,
                "details": details,
            },
        }

    def remember(self, name: str, info: str, source: str = "adapter") -> Dict[str, Any]:
        try:
            # Core's historical update() requires a tracked entity.  Keep that
            # implementation detail inside the adapter so external hosts get a
            # true one-call remember contract.
            slugify = getattr(self.memory, "_slugify", None)
            entities_dir = getattr(self.memory, "entities_dir", None)
            tracked = bool(
                callable(slugify)
                and entities_dir is not None
                and (entities_dir / f"{slugify(name)}.md").exists()
            )
            if not tracked:
                track = getattr(self.memory, "track", None)
                if callable(track):
                    track(name, entity_type="entity", source=source)
            self.memory.update(name, info, source=source)
            return self._ok("remember", name=name)
        except Exception as error:
            return self._error("remember", error)

    def recall(self, query: str, top_k: Optional[int] = None, **kwargs: Any) -> Dict[str, Any]:
        try:
            search_kwargs = dict(kwargs)
            if top_k is not None:
                search_kwargs["top_k"] = top_k
            results = self.memory.search(query, **search_kwargs)
            return self._ok("recall", query=query, results=results)
        except Exception as error:
            return self._error("recall", error)

    def feedback(
        self,
        *,
        classification: str,
        task_ref: str,
        outcome_ref: str,
        input_snapshot_ref: str,
        artifact_kind: str = "memory_policy",
        replayability: str = "partial",
        scope: str = "project",
        evidence_refs=(),
        model_ref: Optional[str] = None,
        tool_refs=(),
        now: Optional[str] = None,
        experience_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record sanitized experience through the existing core contract."""
        try:
            if now is None:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if experience_id is None:
                import hashlib
                identity = f"{classification}|{task_ref}|{outcome_ref}|{input_snapshot_ref}"
                experience_id = "exp." + hashlib.sha256(identity.encode()).hexdigest()[:24]
            record = self.memory.experience_record(
                experience_id,
                artifact_kind=artifact_kind,
                scope=scope,
                task_ref=task_ref,
                input_snapshot_ref=input_snapshot_ref,
                outcome_ref=outcome_ref,
                classification=classification,
                replayability=replayability,
                now=now,
                model_ref=model_ref,
                tool_refs=tool_refs,
                evidence_refs=evidence_refs,
            )
            return self._ok("feedback", record=record)
        except Exception as error:
            return self._error("feedback", error)

    def health(self) -> Dict[str, Any]:
        try:
            return self._ok("health", health=self.memory.health())
        except Exception as error:
            return self._error("health", error)


__all__ = ["MemoryAdapter"]
