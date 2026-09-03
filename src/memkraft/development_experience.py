"""Compile reusable development lessons from completed agent turns.

The compiler is deliberately deterministic and conservative.  It stores no
raw tool arguments or outputs: only coarse route labels and normalized error
classes.  A detour is emitted only when a failed attempt is followed by a
successful verification command in the same completed turn.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


_SECRET_RE = re.compile(
    r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?:sk|ghp|github_pat|xox[baprs])[-_A-Za-z0-9]{8,}|"
    r"(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+"
)
_PATH_RE = re.compile(r"(?:/(?:Users|home)/|/(?:private/)?var/folders/)[^\s'\";,]+")
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:(?:\\+[^\\\s'\";,]+)+")
_ERROR_TYPES = re.compile(
    r"\b(?:AssertionError|ImportError|ModuleNotFoundError|PermissionError|"
    r"TimeoutError|TypeError|ValueError|KeyError|OSError|RuntimeError|"
    r"SyntaxError|ConnectionError)\b"
)
_FAILURE_TEXT = re.compile(
    r"(?i)(?:traceback \(most recent call last\)|\berror\b|\bfailed\b|"
    r"exception|permission denied|timed?\s*out|no such file)"
)
_VERIFY_TARGETS = {"test", "tests", "lint", "build", "check"}
_OBSERVATION_TOOLS = {
    "read_file", "search_files", "web_search", "web_extract", "vision_analyze",
}
_MUTATING_TOOLS = {"patch", "write_file", "skill_manage"}
_READ_ONLY_COMMANDS = {"pwd", "ls", "find", "grep", "rg", "cat", "head", "tail", "du", "df", "ps", "pgrep", "which"}
_READ_ONLY_GIT = {"status", "diff", "log", "show", "rev-parse"}


@dataclass(frozen=True)
class DevelopmentEpisode:
    """One sanitized failure or success lesson ready for ReasoningBank."""

    status: str
    task: str
    route: str
    lesson: str
    error_signature: str = ""
    verified_by: str = ""
    steps: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _Attempt:
    route: str
    success: bool
    error_signature: str
    verification: bool
    tool_name: str
    mutating: bool


def _safe_text(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    text = _SECRET_RE.sub("[REDACTED]", text)
    text = _PATH_RE.sub("[PATH]", text)
    text = _WINDOWS_PATH_RE.sub("[PATH]", text)
    return text[:limit]


def _arguments(call: Dict[str, Any]) -> Dict[str, Any]:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    raw = function.get("arguments", call.get("arguments", {}))
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _tool_name(call: Dict[str, Any]) -> str:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    return str(function.get("name") or call.get("name") or "tool").strip() or "tool"


def _terminal_command(args: Dict[str, Any]) -> List[str]:
    raw = str(args.get("command") or "")
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()
    while tokens and ("=" in tokens[0] and not tokens[0].startswith("=")):
        tokens.pop(0)
    if tokens and tokens[0] in {"env", "/usr/bin/env"}:
        tokens.pop(0)
        while tokens and (tokens[0].startswith("-") or "=" in tokens[0]):
            tokens.pop(0)
    return tokens


def _is_verification(tokens: Sequence[str]) -> bool:
    if not tokens:
        return False
    executable = tokens[0].rsplit("/", 1)[-1].lower()
    args = [token.lower() for token in tokens[1:]]
    if executable in {"pytest", "py.test", "mypy", "pyright", "tox", "nox"}:
        return True
    if executable == "ruff":
        return bool(args and args[0] == "check") or "--check" in args
    if executable.startswith("python"):
        return len(args) >= 2 and args[0] == "-m" and args[1] in {
            "pytest", "unittest", "compileall", "mypy",
        }
    if executable in {"npm", "pnpm", "yarn"}:
        if not args:
            return False
        target = args[1] if args[0] == "run" and len(args) >= 2 else args[0]
        return target in _VERIFY_TARGETS or any(
            target.startswith(prefix) for prefix in ("test:", "lint:", "build:", "check:")
        )
    if executable == "cargo":
        return bool(args and args[0] in {"test", "check", "build", "clippy"})
    if executable == "go":
        return bool(args and args[0] == "test")
    if executable == "uv" and len(args) >= 2 and args[0] == "run":
        return _is_verification(tokens[2:])
    if executable in {"make", "just"}:
        return any(arg in _VERIFY_TARGETS for arg in args)
    if executable == "git":
        return bool(args and args[0] == "diff" and "--check" in args)
    return False


def _route(call: Dict[str, Any]) -> Tuple[str, bool, bool]:
    name = _tool_name(call)
    args = _arguments(call)
    if name not in {"terminal", "execute_code"}:
        return name, False, name in _MUTATING_TOOLS

    tokens = _terminal_command(args)
    if not tokens:
        return name, False, name == "execute_code"
    executable = tokens[0].rsplit("/", 1)[-1]
    label = executable
    if executable.startswith("python") and len(tokens) >= 3 and tokens[1] == "-m":
        label = "{} -m {}".format(executable, tokens[2])
    elif executable in {"git", "npm", "pnpm", "yarn", "cargo", "go", "uv"} and len(tokens) >= 2:
        label = "{} {}".format(executable, tokens[1])
    route = "{}:{}".format(name, label)
    verification = _is_verification(tokens)
    subcommand = tokens[1].lower() if len(tokens) >= 2 else ""
    read_only = (
        verification
        or executable in _READ_ONLY_COMMANDS
        or (executable == "git" and subcommand in _READ_ONLY_GIT)
    )
    return route, verification, not read_only


def _result_payload(content: Any) -> Tuple[Optional[bool], str]:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    parsed: Any = None
    if isinstance(content, dict):
        parsed = content
    elif isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            parsed = None

    explicit_status: Optional[bool] = None
    detail = text
    if isinstance(parsed, dict):
        if isinstance(parsed.get("success"), bool):
            explicit_status = bool(parsed["success"])
        exit_code = parsed.get("exit_code")
        if exit_code is not None:
            try:
                explicit_status = int(exit_code) == 0
            except (TypeError, ValueError):
                explicit_status = False
        if parsed.get("error"):
            explicit_status = False
        detail = " ".join(
            str(parsed.get(key) or "") for key in ("error", "output", "message", "content")
        )
    if explicit_status is True:
        return True, ""
    failed = explicit_status is False or bool(_FAILURE_TEXT.search(detail or ""))

    if not failed:
        return None, ""
    error_match = _ERROR_TYPES.search(detail or "")
    if error_match:
        return False, error_match.group(0)
    lowered = (detail or "").lower()
    for signature, markers in (
        ("permission-denied", ("permission denied", "not permitted")),
        ("timeout", ("timed out", "timeout")),
        ("not-found", ("no such file", "not found")),
        ("test-failure", ("failed", "assert")),
    ):
        if any(marker in lowered for marker in markers):
            return False, signature
    return False, "tool-error"


def _current_turn(messages: Sequence[Any], task: str) -> List[Dict[str, Any]]:
    valid_messages = [message for message in messages if isinstance(message, dict)]
    start = -1
    normalized_task = " ".join(str(task or "").split())
    for index, message in enumerate(valid_messages):
        if message.get("role") != "user":
            continue
        normalized_content = " ".join(str(message.get("content") or "").split())
        if normalized_content == normalized_task:
            start = index
    if start < 0:
        for index in range(len(valid_messages) - 1, -1, -1):
            if valid_messages[index].get("role") == "user":
                start = index
                break
    return valid_messages[start + 1 :] if start >= 0 else valid_messages


def _attempts(messages: Sequence[Dict[str, Any]]) -> List[_Attempt]:
    calls: Dict[str, Dict[str, Any]] = {}
    ordered_ids: List[str] = []
    results: Dict[str, Any] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id") or "call-{}".format(len(ordered_ids)))
                calls[call_id] = call
                ordered_ids.append(call_id)
        elif message.get("role") == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if call_id:
                results[call_id] = message.get("content", "")

    out: List[_Attempt] = []
    for call_id in ordered_ids:
        if call_id not in results:
            continue
        call = calls[call_id]
        route, verification, mutating = _route(call)
        explicit_success, signature = _result_payload(results[call_id])
        success = explicit_success if explicit_success is not None else not verification
        if explicit_success is None and verification:
            signature = "unverified-result"
        out.append(_Attempt(route, success, signature, verification, _tool_name(call), mutating))
    return out


def compile_development_experience(
    task: str,
    messages: Sequence[Dict[str, Any]],
    *,
    session_id: str = "",
) -> List[DevelopmentEpisode]:
    """Compile detours only when a later successful verifier proves recovery."""

    del session_id  # Reserved for deterministic persistence identifiers.
    task_text = _safe_text(task) or "development task"
    attempts = _attempts(_current_turn(messages, task))
    verification_attempts = [
        (index, attempt)
        for index, attempt in enumerate(attempts)
        if attempt.verification
    ]
    if not verification_attempts:
        return []
    verifier_index, verifier = verification_attempts[-1]
    if not verifier.success or not any(not prior.success for prior in attempts[:verifier_index]):
        return []
    if any(attempt.mutating for attempt in attempts[verifier_index + 1 :]):
        return []

    failures = [attempt for attempt in attempts[:verifier_index] if not attempt.success]
    if not failures:
        return []
    episodes: List[DevelopmentEpisode] = []
    for failure in failures:
        lesson = "For '{}', avoid {}; it failed with {}.".format(
            task_text, failure.route, failure.error_signature
        )
        episodes.append(
            DevelopmentEpisode(
                status="failure",
                task=task_text,
                route=failure.route,
                lesson=lesson,
                error_signature=failure.error_signature,
                steps=(failure.route,),
            )
        )

    route_steps: List[str] = []
    last_failure_index = max(index for index, attempt in enumerate(attempts[:verifier_index]) if not attempt.success)
    for attempt in attempts[last_failure_index + 1 : verifier_index + 1]:
        if not attempt.success or attempt.tool_name in _OBSERVATION_TOOLS:
            continue
        if not route_steps or route_steps[-1] != attempt.route:
            route_steps.append(attempt.route)
    if not route_steps or route_steps[-1] != verifier.route:
        route_steps.append(verifier.route)
    success_route = " -> ".join(route_steps)
    lesson = "For '{}', reuse {}; verified by {}.".format(
        task_text, success_route, verifier.route
    )
    episodes.append(
        DevelopmentEpisode(
            status="success",
            task=task_text,
            route=success_route,
            lesson=lesson,
            verified_by=verifier.route,
            steps=tuple(route_steps),
        )
    )
    return episodes


_CAPTURE_LOCK = threading.RLock()


class DevelopmentExperienceMixin:
    """Persist and retrieve conservative, tool-derived development lessons."""

    def development_capture_turn(
        self,
        task: str,
        messages: Sequence[Dict[str, Any]],
        *,
        session_id: str = "",
    ) -> Dict[str, Any]:
        """Compile and persist verified detours from one completed turn."""

        from .reasoning_bank import _read_jsonl

        episodes = compile_development_experience(task, messages, session_id=session_id)
        semantic_turn = {
            "task": _safe_text(task),
            "episodes": [
                {
                    "status": episode.status,
                    "route": episode.route,
                    "error": episode.error_signature,
                    "verified_by": episode.verified_by,
                    "steps": list(episode.steps),
                }
                for episode in episodes
            ],
        }
        turn_fingerprint = hashlib.sha256(
            json.dumps(semantic_turn, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:24]
        captured = 0
        duplicates = 0
        task_ids: List[str] = []
        with _CAPTURE_LOCK:
            for occurrence, episode in enumerate(episodes):
                seed = "\x1f".join(
                    (
                        session_id,
                        turn_fingerprint,
                        str(occurrence),
                        episode.task,
                        episode.status,
                        episode.route,
                    )
                )
                task_id = "dev-{}-{}".format(
                    episode.status,
                    hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24],
                )
                task_ids.append(task_id)
                signature = "{}::{}".format(
                    episode.route,
                    episode.error_signature or episode.verified_by or episode.status,
                )
                path = self._trajectory_path(task_id)
                if any(
                    row.get("kind") == "complete"
                    and row.get("status") == episode.status
                    and row.get("pattern_signature") == signature
                    for row in _read_jsonl(path)
                ):
                    duplicates += 1
                    continue

                tags = ["development-experience", "auto-captured", episode.status]
                self.trajectory_start(task_id, title=episode.task, tags=tags)
                for step, action in enumerate(episode.steps, start=1):
                    if episode.status == "failure":
                        outcome = episode.error_signature
                    elif action == episode.verified_by:
                        outcome = "verified"
                    else:
                        outcome = "succeeded"
                    self.trajectory_log(
                        task_id,
                        step,
                        action=action,
                        outcome=outcome,
                        metadata={"auto_captured": True},
                    )
                self.trajectory_complete(
                    task_id,
                    status=episode.status,
                    lesson=episode.lesson,
                    pattern_signature=signature,
                    tags=tags,
                )
                captured += 1
        return {"captured": captured, "duplicates": duplicates, "task_ids": task_ids}

    def development_inject_for_task(
        self,
        task_query: str,
        *,
        k: int = 3,
        max_chars: int = 1400,
        style: str = "compact",
    ) -> str:
        """Return bounded failure/success guidance for a similar new task."""

        return self.reasoning_inject_for_task(
            task_query,
            k=k,
            max_chars=max_chars,
            style=style,
        )


__all__ = [
    "DevelopmentEpisode",
    "DevelopmentExperienceMixin",
    "compile_development_experience",
]
