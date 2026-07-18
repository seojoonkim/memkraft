"""Fail-closed deterministic execution for authorized ReasoningBank trajectories.

Authorization seals are process-local.  They detect ordinary object and store
mutation, but are not a sandbox against code with access to this process, its
key, a debugger, module monkeypatching, or inherited state after ``fork``.
Seals expire when this module is reloaded or the process restarts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Match, Optional, Tuple

__all__ = ["ReasoningAuthorization", "ReasoningExecutionResult"]

MAX_DIGITS = 100
MAX_ITEMS = 8
MAX_DECLARED_PRIME = 1_000_000
MAX_GRID_SIDE = 1_000
MAX_TRAJECTORY_BYTES = 64 * 1024
MAX_TRAJECTORY_LINES = 256
MAX_AUTHORIZATION_ENTRIES = 256
MAX_TASK_CHARS = 4096
_PARSER_IDENTITY = "memkraft.reasoning_execution.exact-grammar.v1"
_IDS = (
    "A.inclusion_exclusion_sum",
    "B.legendre_factorial_exponent",
    "C.shortest_grid_paths",
    "D.divisor_count_prime_powers",
    "E.sum_squares_or_cubes",
    "F.modular_exponentiation",
)


def _registry_digest(procedure_id: str, version: int) -> str:
    raw = json.dumps(
        {
            "parser_identity": _PARSER_IDENTITY,
            "procedure_id": procedure_id,
            "version": version,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


_REGISTRY = {
    procedure_id: {"version": 1, "registry_digest": _registry_digest(procedure_id, 1)}
    for procedure_id in _IDS
}
_AUTHORIZATION_KEY = secrets.token_bytes(32)


def _canonical_task_id(value: Any) -> bool:
    """Match the writer's deterministic slug result, excluding UUID cases."""
    if not isinstance(value, str) or not value or not value.strip():
        return False
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", value.strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return bool(slug) and slug[:120] == value


def _iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


@dataclass(frozen=True)
class _AuthorizationEntry:
    task_id: str
    path: str
    content_sha256: str
    procedure_id: str
    procedure_version: int
    registry_digest: str


@dataclass(frozen=True)
class ReasoningAuthorization:
    """Opaque process-local authorization for exact completed trajectory bytes."""

    entries: Tuple[_AuthorizationEntry, ...]
    seal: str


@dataclass(frozen=True)
class ReasoningExecutionResult:
    """Result and accounting for deterministic execution or model fallback."""

    answer: str
    route: str
    procedure_id: Optional[str]
    retrieval_score: Optional[float]
    latency_ms: float
    model_calls: int
    reason: str


def _procedure_ref(procedure_id: str) -> Dict[str, Any]:
    entry = _REGISTRY[procedure_id]
    return {
        "id": procedure_id,
        "version": entry["version"],
        "registry_digest": entry["registry_digest"],
    }


def _entry_valid(entry: Any) -> bool:
    if type(entry) is not _AuthorizationEntry:
        return False
    if not _canonical_task_id(entry.task_id):
        return False
    if not isinstance(entry.path, str) or not Path(entry.path).is_absolute():
        return False
    if (
        not isinstance(entry.content_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", entry.content_sha256) is None
    ):
        return False
    if (
        not isinstance(entry.procedure_id, str)
        or type(entry.procedure_version) is not int
    ):
        return False
    if (
        not isinstance(entry.registry_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", entry.registry_digest) is None
    ):
        return False
    registry_item = _REGISTRY.get(entry.procedure_id)
    return registry_item == {
        "version": entry.procedure_version,
        "registry_digest": entry.registry_digest,
    }


def _authorization_payload(entries: Tuple[_AuthorizationEntry, ...]) -> bytes:
    return json.dumps(
        [
            {
                "content_sha256": item.content_sha256,
                "path": item.path,
                "procedure_id": item.procedure_id,
                "procedure_version": item.procedure_version,
                "registry_digest": item.registry_digest,
                "task_id": item.task_id,
            }
            for item in entries
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _seal(entries: Tuple[_AuthorizationEntry, ...]) -> str:
    return hmac.new(
        _AUTHORIZATION_KEY, _authorization_payload(entries), hashlib.sha256
    ).hexdigest()


def _authorization_valid(value: Any) -> bool:
    try:
        if type(value) is not ReasoningAuthorization or type(value.entries) is not tuple:
            return False
        if len(value.entries) > MAX_AUTHORIZATION_ENTRIES or any(
            not _entry_valid(item) for item in value.entries
        ):
            return False
        task_ids = [item.task_id for item in value.entries]
        if len(task_ids) != len(set(task_ids)):
            return False
        if (
            not isinstance(value.seal, str)
            or re.fullmatch(r"[0-9a-f]{64}", value.seal) is None
        ):
            return False
        return hmac.compare_digest(value.seal, _seal(value.entries))
    except Exception:
        return False


def _rotate_authorization_key_for_tests() -> None:
    """Invalidate existing seals; intended only for restart-boundary tests."""
    global _AUTHORIZATION_KEY
    _AUTHORIZATION_KEY = secrets.token_bytes(32)


_DESCRIPTOR_SUPPORTED = (
    bool(getattr(os, "O_DIRECTORY", 0))
    and bool(getattr(os, "O_NOFOLLOW", 0))
    and os.open in getattr(os, "supports_dir_fd", set())
)


def _base_text(base_dir: Any) -> str:
    """Convert the configured base, never a recalled path-like object."""
    if type(base_dir) is str:
        return base_dir
    if isinstance(base_dir, Path):
        return str(base_dir)
    raise ValueError("invalid trajectory base")


def _lexical_base(base_dir: Any) -> str:
    return os.path.abspath(os.path.normpath(_base_text(base_dir)))


def _read_anchored(base_dir: Any, task_id: str) -> Tuple[bytes, str]:
    """Read through a no-follow directory-FD chain rooted at configured base."""
    if not _DESCRIPTOR_SUPPORTED:
        raise ValueError("descriptor traversal unsupported")
    base_text = _base_text(base_dir)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    try:
        base_fd = os.open(base_text, directory_flags)
        descriptors.append(base_fd)
        if not stat.S_ISDIR(os.fstat(base_fd).st_mode):
            raise ValueError("invalid trajectory base")
        memkraft_fd = os.open(
            ".memkraft", directory_flags, dir_fd=base_fd
        )
        descriptors.append(memkraft_fd)
        if not stat.S_ISDIR(os.fstat(memkraft_fd).st_mode):
            raise ValueError("invalid trajectory root")
        trajectories_fd = os.open(
            "trajectories", directory_flags, dir_fd=memkraft_fd
        )
        descriptors.append(trajectories_fd)
        if not stat.S_ISDIR(os.fstat(trajectories_fd).st_mode):
            raise ValueError("invalid trajectory root")
        filename = task_id + ".jsonl"
        file_fd = os.open(
            filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=trajectories_fd
        )
        descriptors.append(file_fd)
        opened = os.fstat(file_fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("invalid trajectory file")
        if opened.st_size > MAX_TRAJECTORY_BYTES:
            raise ValueError("trajectory too large")
        chunks = []
        remaining = MAX_TRAJECTORY_BYTES + 1
        while remaining:
            chunk = os.read(file_fd, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_TRAJECTORY_BYTES or os.read(file_fd, 1):
            raise ValueError("trajectory too large")
        identity = os.path.join(
            _lexical_base(base_dir), ".memkraft", "trajectories", filename
        )
        return raw, identity
    finally:
        primary_exception = sys.exc_info()[0] is not None
        close_error = None
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except Exception as error:
                if close_error is None:
                    close_error = error
        if close_error is not None and not primary_exception:
            raise close_error


def _parse_trajectory(
    raw: bytes, task_id: str, requested_procedure: Optional[str] = None
):
    lines = raw.splitlines()
    if not lines or len(lines) > MAX_TRAJECTORY_LINES:
        raise ValueError("invalid trajectory line count")
    parsed = []
    for line in lines:
        try:
            record = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
            raise ValueError("invalid trajectory encoding") from error
        if not isinstance(record, dict):
            raise ValueError("trajectory record is not an object")
        parsed.append(record)
    if any(
        record.get("kind") not in {"start", "step", "complete"} for record in parsed
    ):
        raise ValueError("invalid trajectory record kind")
    starts = [record for record in parsed if record.get("kind") == "start"]
    completes = [record for record in parsed if record.get("kind") == "complete"]
    if len(starts) != 1 or len(completes) != 1:
        raise ValueError("invalid trajectory terminal count")
    if parsed[0] is not starts[0] or parsed[-1] is not completes[0]:
        raise ValueError("invalid trajectory order")
    if any(record.get("task_id") != task_id for record in parsed):
        raise ValueError("trajectory identity mismatch")
    start = starts[0]
    complete = completes[0]
    if set(start) != {
        "kind",
        "task_id",
        "title",
        "tags",
        "started_at",
        "schema_version",
    }:
        raise ValueError("invalid start shape")
    if (
        type(start["schema_version"]) is not int
        or start["schema_version"] != 1
        or not isinstance(start["title"], str)
        or not _string_list(start["tags"])
        or not _iso_timestamp(start["started_at"])
    ):
        raise ValueError("invalid start values")
    if set(complete) != {
        "kind",
        "task_id",
        "status",
        "lesson",
        "pattern_signature",
        "tags",
        "completed_at",
    }:
        raise ValueError("invalid complete shape")
    if (
        complete["status"] != "success"
        or not isinstance(complete["lesson"], str)
        or not isinstance(complete["pattern_signature"], str)
        or not _string_list(complete["tags"])
        or not _iso_timestamp(complete["completed_at"])
    ):
        raise ValueError("trajectory is not successful")
    references = []
    steps = parsed[1:-1]
    if not steps:
        raise ValueError("trajectory has no steps")
    previous_step = 0
    for record in steps:
        if record.get("kind") != "step" or set(record) != {
            "kind",
            "task_id",
            "step",
            "thought",
            "action",
            "outcome",
            "metadata",
            "ts",
        }:
            raise ValueError("invalid step shape")
        metadata = record["metadata"]
        if (
            type(record["step"]) is not int
            or record["step"] <= previous_step
            or not isinstance(record["thought"], str)
            or not isinstance(record["action"], str)
            or not isinstance(record["outcome"], str)
            or not isinstance(metadata, dict)
            or not _iso_timestamp(record["ts"])
        ):
            raise ValueError("invalid step values")
        previous_step = record["step"]
        if "procedure_ref" in metadata:
            references.append(metadata["procedure_ref"])
    if len(references) != 1:
        raise ValueError("invalid procedure reference count")
    reference = references[0]
    if not isinstance(reference, dict) or set(reference) != {
        "id",
        "version",
        "registry_digest",
    }:
        raise ValueError("invalid procedure reference shape")
    procedure_id = reference.get("id")
    if (
        not isinstance(procedure_id, str)
        or type(reference.get("version")) is not int
        or not isinstance(reference.get("registry_digest"), str)
        or procedure_id not in _REGISTRY
    ):
        raise ValueError("unknown procedure reference")
    if reference != _procedure_ref(procedure_id):
        raise ValueError("procedure reference mismatch")
    if requested_procedure is not None and procedure_id != requested_procedure:
        raise ValueError("requested procedure mismatch")
    return parsed, reference


def _build_authorization(
    base_dir: Any, completed: Iterable[Tuple[str, str]]
) -> ReasoningAuthorization:
    if isinstance(completed, (str, bytes)):
        raise TypeError("completed must contain tuple pairs")
    try:
        iterator = iter(completed)
    except TypeError as error:
        raise TypeError("completed must be iterable") from error
    authorized = []
    seen = set()
    for index, item in enumerate(iterator):
        if not _DESCRIPTOR_SUPPORTED:
            raise ValueError("descriptor traversal unsupported")
        if index >= MAX_AUTHORIZATION_ENTRIES:
            raise ValueError("too many authorization entries")
        if type(item) is not tuple or len(item) != 2:
            raise TypeError("authorization entry must be an exact tuple pair")
        task_id, procedure_id = item
        if not _canonical_task_id(task_id):
            raise ValueError("task ID is not canonical")
        if task_id in seen:
            raise ValueError("duplicate task ID")
        if not isinstance(procedure_id, str):
            raise TypeError("procedure ID must be a string")
        if procedure_id not in _REGISTRY:
            raise ValueError("unknown procedure ID")
        try:
            raw, identity = _read_anchored(base_dir, task_id)
            _parse_trajectory(raw, task_id, procedure_id)
        except Exception as error:
            raise ValueError("invalid trajectory file") from error
        reference = _procedure_ref(procedure_id)
        authorized.append(
            _AuthorizationEntry(
                task_id=task_id,
                path=identity,
                content_sha256=hashlib.sha256(raw).hexdigest(),
                procedure_id=procedure_id,
                procedure_version=reference["version"],
                registry_digest=reference["registry_digest"],
            )
        )
        seen.add(task_id)
    entries = tuple(authorized)
    return ReasoningAuthorization(entries=entries, seal=_seal(entries))


def _integer(text: str, positive: bool = False) -> int:
    if not text or len(text) > MAX_DIGITS or not text.isascii() or not text.isdecimal():
        raise ValueError("invalid integer")
    value = int(text)
    if positive and value <= 0:
        raise ValueError("integer must be positive")
    return value


def _is_prime(value: int) -> bool:
    if value < 2 or value > MAX_DECLARED_PRIME:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def _legendre(n: int, prime: int) -> int:
    total = 0
    while n:
        n //= prime
        total += n
    return total


def _run_a(match: Match[str]) -> int:
    limit = _integer(match["limit"], True)
    text = match["divisors"]
    pieces = (
        text.split(" or ")
        if " or " in text and "," not in text
        else re.split(r", or |, ", text)
    )
    divisors = [_integer(piece, True) for piece in pieces]
    if not 2 <= len(divisors) <= MAX_ITEMS:
        raise ValueError("invalid divisor count")
    if len(divisors) != len(set(divisors)) or any(value == 1 for value in divisors):
        raise ValueError("invalid divisor")
    total = 0
    for mask in range(1, 1 << len(divisors)):
        common = 1
        selected = 0
        for index, divisor in enumerate(divisors):
            if mask & (1 << index):
                common = math.lcm(common, divisor)
                selected += 1
        count = (limit - 1) // common
        subtotal = common * count * (count + 1) // 2
        total += subtotal if selected % 2 else -subtotal
    return total


def _run_b(match: Match[str]) -> int:
    trailing = match["trailing_n"]
    if trailing is not None:
        return _legendre(_integer(trailing, True), 5)
    prime = _integer(match["prime"], True)
    if not _is_prime(prime):
        raise ValueError("invalid prime")
    return _legendre(_integer(match["exponent_n"], True), prime)


def _run_c(match: Match[str]) -> int:
    rows = _integer(match["rows"], True)
    columns = _integer(match["columns"], True)
    if rows > MAX_GRID_SIDE or columns > MAX_GRID_SIDE:
        raise ValueError("grid too large")
    return math.comb(rows + columns, rows)


def _run_d(match: Match[str]) -> int:
    pieces = match["factors"].split(" * ")
    if not 1 <= len(pieces) <= MAX_ITEMS:
        raise ValueError("invalid factor count")
    primes = []
    result = 1
    for piece in pieces:
        base_text, exponent_text = piece.split("^")
        base = _integer(base_text, True)
        exponent = _integer(exponent_text, True)
        if not _is_prime(base):
            raise ValueError("invalid prime")
        primes.append(base)
        result *= exponent + 1
    if len(primes) != len(set(primes)):
        raise ValueError("duplicate prime")
    return result


def _run_e(match: Match[str]) -> int:
    n = _integer(match["n"], True)
    if match["power"] == "squares":
        return n * (n + 1) * (2 * n + 1) // 6
    return (n * (n + 1) // 2) ** 2


def _run_f(match: Match[str]) -> int:
    base = _integer(match["base"])
    exponent = _integer(match["exponent"])
    modulus = _integer(match["modulus"], True)
    if modulus <= 1:
        raise ValueError("invalid modulus")
    return pow(base, exponent, modulus)


_A_DIVISORS = r"(?P<divisors>\d+(?: or \d+|(?:, \d+)+, or \d+))"
_PROCEDURES = {
    "A.inclusion_exclusion_sum": (
        re.compile(
            rf"Sum positive integers below (?P<limit>\d+) divisible by {_A_DIVISORS}\."
        ),
        _run_a,
    ),
    "B.legendre_factorial_exponent": (
        re.compile(
            r"(?:Find the trailing zeroes in (?P<trailing_n>\d+) factorial\.|"
            r"Find the exponent of (?P<prime>\d+) in the prime factorization of "
            r"(?P<exponent_n>\d+) factorial\.)"
        ),
        _run_b,
    ),
    "C.shortest_grid_paths": (
        re.compile(
            r"Count shortest right/down paths across a (?P<rows>\d+) by (?P<columns>\d+) grid\."
        ),
        _run_c,
    ),
    "D.divisor_count_prime_powers": (
        re.compile(
            r"Count positive divisors of (?P<factors>\d+\^\d+(?: \* \d+\^\d+)*)\."
        ),
        _run_d,
    ),
    "E.sum_squares_or_cubes": (
        re.compile(r"Sum the (?P<power>squares|cubes) from 1 through (?P<n>\d+)\."),
        _run_e,
    ),
    "F.modular_exponentiation": (
        re.compile(
            r"Compute (?P<base>\d+)\^(?P<exponent>\d+) modulo (?P<modulus>\d+)\."
        ),
        _run_f,
    ),
}


def _fallback(
    task: Any,
    fallback: Callable[[str], str],
    started: float,
    reason: str,
    score: Optional[float] = None,
    procedure_id: Optional[str] = None,
) -> ReasoningExecutionResult:
    answer = fallback(task)
    if not isinstance(answer, str):
        raise TypeError("fallback answer must be a string")
    return ReasoningExecutionResult(
        answer=answer,
        route="model_fallback",
        procedure_id=procedure_id,
        retrieval_score=score,
        latency_ms=max(0.0, (time.perf_counter() - started) * 1000.0),
        model_calls=1,
        reason=reason,
    )


def _verify_hit(
    base_dir: Any, hit: Dict[str, Any], authorization: ReasoningAuthorization
):
    task_id = hit.get("task_id")
    hit_path = hit.get("path")
    if not _canonical_task_id(task_id):
        raise ValueError("invalid hit identity")
    if type(hit_path) is not str or not hit_path:
        raise ValueError("invalid hit path")
    if ".." in Path(hit_path).parts:
        raise ValueError("path traversal")
    identity = os.path.join(
        _lexical_base(base_dir), ".memkraft", "trajectories", task_id + ".jsonl"
    )
    supplied_identity = os.path.abspath(os.path.normpath(hit_path))
    if supplied_identity != identity:
        raise ValueError("noncanonical trajectory path")
    raw, anchored_identity = _read_anchored(base_dir, task_id)
    if anchored_identity != identity:
        raise ValueError("trajectory identity mismatch")
    matches = [item for item in authorization.entries if item.task_id == task_id]
    if len(matches) != 1:
        raise LookupError("trajectory is not authorized")
    item = matches[0]
    if item.path != identity or not hmac.compare_digest(
        item.content_sha256, hashlib.sha256(raw).hexdigest()
    ):
        raise PermissionError("authorization binding mismatch")
    parsed, reference = _parse_trajectory(raw, task_id)
    start = parsed[0]
    complete = parsed[-1]
    for hit_key, record, record_key in (
        ("title", start, "title"),
        ("lesson", complete, "lesson"),
        ("pattern_signature", complete, "pattern_signature"),
    ):
        if type(hit.get(hit_key)) is not str or hit[hit_key] != record.get(record_key):
            raise ValueError("recall summary mismatch")
    if (
        item.procedure_id != reference["id"]
        or item.procedure_version != reference["version"]
        or item.registry_digest != reference["registry_digest"]
    ):
        raise PermissionError("authorization binding mismatch")
    return item.procedure_id


def _reasoning_execute(
    owner: Any,
    task: str,
    authorization: Optional[ReasoningAuthorization],
    fallback: Callable[[str], str],
) -> ReasoningExecutionResult:
    started = time.perf_counter()
    if type(task) is not str:
        return _fallback(task, fallback, started, "task_invalid")
    try:
        hits = owner.reasoning_recall(task, top_k=1, status="success")
    except Exception:
        return _fallback(task, fallback, started, "retrieval_error")
    if type(hits) is not list:
        return _fallback(task, fallback, started, "recall_malformed")
    if not hits:
        return _fallback(task, fallback, started, "recall_empty")
    hit = hits[0]
    if type(hit) is not dict:
        return _fallback(task, fallback, started, "recall_malformed")
    try:
        status_valid = type(hit.get("status")) is str and hit.get("status") == "success"
    except Exception:
        status_valid = False
    if not status_valid:
        return _fallback(task, fallback, started, "recall_malformed")
    try:
        value = hit.get("score")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("invalid score")
        score = float(value)
        if not math.isfinite(score) or score <= 0.0:
            raise ValueError("invalid score")
    except Exception:
        return _fallback(task, fallback, started, "recall_score_invalid")
    authorization_reason = None
    try:
        if authorization is None:
            authorization_reason = "authorization_missing"
        elif not _authorization_valid(authorization):
            authorization_reason = "authorization_invalid"
        elif not authorization.entries:
            authorization_reason = "authorization_missing"
    except Exception:
        authorization_reason = "authorization_invalid"
    if authorization_reason is not None:
        return _fallback(task, fallback, started, authorization_reason, score)
    try:
        procedure_id = _verify_hit(owner.base_dir, hit, authorization)
    except LookupError:
        return _fallback(task, fallback, started, "authorization_missing", score)
    except PermissionError:
        return _fallback(
            task, fallback, started, "authorization_binding_mismatch", score
        )
    except Exception:
        return _fallback(task, fallback, started, "trajectory_invalid", score)
    if len(task) > MAX_TASK_CHARS:
        return _fallback(
            task, fallback, started, "grammar_mismatch", score, procedure_id
        )
    try:
        pattern, runner = _PROCEDURES[procedure_id]
        match = pattern.fullmatch(task)
    except Exception:
        return _fallback(task, fallback, started, "trajectory_invalid", score)
    if match is None:
        return _fallback(
            task, fallback, started, "grammar_mismatch", score, procedure_id
        )
    try:
        answer = str(runner(match))
    except (ValueError, OverflowError):
        return _fallback(
            task, fallback, started, "safety_constraint", score, procedure_id
        )
    return ReasoningExecutionResult(
        answer=answer,
        route="executor",
        procedure_id=procedure_id,
        retrieval_score=score,
        latency_ms=max(0.0, (time.perf_counter() - started) * 1000.0),
        model_calls=0,
        reason="executor_executed",
    )
