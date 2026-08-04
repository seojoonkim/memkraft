"""MKCJSON/1 canonical JSON, digests, and the MKEP-TIME/1 profile (plan §5).

Full RFC 8785 is not implementable stdlib-only on Python 3.9 — JCS requires
ECMAScript number serialization, and CPython's float repr diverges from ES6 on
exponent formatting. MKCJSON/1 is the strict subset chosen so those cases
cannot arise: integers only, ASCII object keys, NFC strings.

The bytes this module produces are a wire contract. A second-language runtime
has to reproduce them exactly, so every rule here is deliberate and the pinned
vectors in ``tests/test_execution_protocol.py`` are the specification.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

__all__ = [
    "ExecutionError",
    "ValidationError",
    "ConflictError",
    "NotDeclaredError",
    "EvidenceError",
    "MAX_SAFE_INTEGER",
    "MIN_SAFE_INTEGER",
    "MAX_DEPTH",
    "MAX_KEYS_PER_OBJECT",
    "MAX_ARRAY_LENGTH",
    "KEY_PATTERN",
    "mkcjson",
    "digest",
    "parse_timestamp",
    "canonical_timestamp",
]

# §5.2 rule 2, rule 9.
MAX_SAFE_INTEGER = 2 ** 53 - 1
MIN_SAFE_INTEGER = -(2 ** 53 - 1)
MAX_DEPTH = 8
MAX_KEYS_PER_OBJECT = 64
MAX_ARRAY_LENGTH = 32

# §5.2 rule 3. ASCII-only, so code-point, byte, and UTF-16 sort orders coincide
# across Python, Go, Rust, and JavaScript — this is what makes ``sort_keys``
# safe and the cross-language digest claim true.
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# §6.7 error registry, restricted to the codes the shipped modules can raise.
# Stable, closed, additive-only: a code is never repurposed.
_ERROR_REGISTRY = {
    "E_TYPE": ("input", False),
    "E_PATTERN": ("input", False),
    "E_MISSING_FIELD": ("input", False),
    "E_TIME_NAIVE": ("input", False),
    "E_TIME_FORMAT": ("input", False),
    "E_LIMIT_EXCEEDED": ("limits", False),
    "E_GATE_CAP": ("limits", False),
    "E_NOT_DECLARED": ("state", False),
    "E_ALREADY_DECLARED": ("state", False),
    "E_INVALID_TRANSITION": ("state", False),
    "E_IDEMPOTENCY_MISMATCH": ("idempotency", False),
    "E_EVIDENCE_REQUIRED": ("evidence", False),
    "E_EVIDENCE_STALE": ("evidence", False),
    "E_AUTHORITY_CLAIM_REQUIRED": ("evidence", False),
    "E_AUTHORITY_VERIFIED_FORBIDDEN": ("evidence", False),
}


class ExecutionError(ValueError):
    """A protocol error carrying its stable wire ``code``.

    Inherits ``ValueError`` so existing broad-catch callers keep working, and
    exposes the same ``code`` string the wire returns, so a Python caller and a
    CLI caller branch on identical values.
    """

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        error_class, retryable = _ERROR_REGISTRY[code]
        self.code = code
        self.error_class = error_class
        self.retryable = retryable
        self.details = dict(details or {})


class ValidationError(ExecutionError):
    """Input rejected before any state was touched."""


class ConflictError(ExecutionError):
    """The log already holds an incompatible record for this identity or key."""


class NotDeclaredError(ExecutionError):
    """The referenced goal or gate was never declared."""


class EvidenceError(ExecutionError):
    """An evidence or authority rule was violated."""


def _fail(code, message, path=None, **extra):
    details = dict(extra)
    if path is not None:
        details["path"] = path
    raise ValidationError(code, message, details)


def _has_lone_surrogate(text: str) -> bool:
    return any(0xD800 <= ord(char) <= 0xDFFF for char in text)


def _check(obj: Any, path: str = "", depth: int = 1) -> Any:
    """Enforce §5.2 rules 2–5 and 9, returning the normalized value.

    Normalization is limited to NFC on strings; everything else either passes
    through unchanged or raises. ``depth`` counts containers, starting at 1 for
    the top-level object.
    """
    if isinstance(obj, (dict, list)) and depth > MAX_DEPTH:
        # Depth counts containers, not the scalars they hold: the top-level
        # object is depth 1, so eight nested objects are legal and nine are not.
        _fail("E_LIMIT_EXCEEDED", "nesting deeper than %d" % MAX_DEPTH, path or "$")

    if obj is None or obj is True or obj is False:
        return obj

    if isinstance(obj, int):  # after the bool check: bool is a subclass of int
        if not MIN_SAFE_INTEGER <= obj <= MAX_SAFE_INTEGER:
            _fail("E_LIMIT_EXCEEDED",
                  "integer outside the safe range", path or "$", value=str(obj))
        return obj

    if isinstance(obj, float):
        _fail("E_TYPE", "floats are not representable in MKCJSON/1", path or "$")

    if isinstance(obj, str):
        if _has_lone_surrogate(obj):
            _fail("E_TYPE", "string contains a lone surrogate", path or "$")
        return unicodedata.normalize("NFC", obj)

    if isinstance(obj, dict):
        if len(obj) > MAX_KEYS_PER_OBJECT:
            _fail("E_LIMIT_EXCEEDED",
                  "more than %d keys" % MAX_KEYS_PER_OBJECT, path or "$")
        checked = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                _fail("E_TYPE", "object keys must be strings", path or "$")
            if not KEY_PATTERN.match(key):
                _fail("E_PATTERN", "key %r does not match %s" % (key, KEY_PATTERN.pattern),
                      path or "$", key=key)
            child = "%s.%s" % (path, key) if path else key
            checked[key] = _check(value, child, depth + 1)
        return checked

    if isinstance(obj, list):
        if len(obj) > MAX_ARRAY_LENGTH:
            _fail("E_LIMIT_EXCEEDED",
                  "array longer than %d" % MAX_ARRAY_LENGTH, path or "$")
        return [
            _check(item, "%s[%d]" % (path or "$", index), depth + 1)
            for index, item in enumerate(obj)
        ]

    _fail("E_TYPE", "unsupported type %s" % type(obj).__name__, path or "$")


def mkcjson(obj: Any) -> bytes:
    """Return the MKCJSON/1 canonical UTF-8 bytes of ``obj``.

    ``sort_keys=True`` is correct only because rule 3 confines keys to ASCII.
    """
    if not isinstance(obj, dict):
        _fail("E_TYPE", "the canonical form is defined over objects only", "$")
    return json.dumps(
        _check(obj), ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def digest(obj: Any) -> str:
    """Return ``hex(sha256(mkcjson(obj)))``, lowercase, 64 characters.

    Always over the canonical form of the *logical* object. Never over a stored
    file line: ``store_core.append`` writes without ``sort_keys``, so its bytes
    are not canonical bytes (conformance case CJ-06).
    """
    return hashlib.sha256(mkcjson(obj)).hexdigest()


# --------------------------------------------------------------------------
# MKEP-TIME/1 (§5.6)
# --------------------------------------------------------------------------

_TIMESTAMP_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"T(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d{1,6}))?"
    r"(?P<offset>Z|[+-]\d{2}:\d{2})$"
)


def parse_timestamp(value: Any) -> datetime:
    """Parse a MKEP-TIME/1 wire timestamp into an aware ``datetime``.

    ``datetime.fromisoformat`` does not accept a ``Z`` suffix on Python 3.9, so
    ``Z`` is substituted before parsing (conformance case TM-03).
    """
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            _fail("E_TIME_NAIVE", "timestamp has no UTC offset")
        return value

    if not isinstance(value, str):
        _fail("E_TYPE", "timestamp must be a string or an aware datetime")

    match = _TIMESTAMP_PATTERN.match(value)
    if match is None:
        # A well-formed timestamp that merely lacks an offset gets the specific
        # code, because "you forgot the offset" and "this is not a timestamp"
        # are different bugs for the caller.
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?$", value):
            _fail("E_TIME_NAIVE", "timestamp has no UTC offset")
        _fail("E_TIME_FORMAT", "timestamp does not match MKEP-TIME/1")

    if int(match.group("second")) == 60:
        _fail("E_TIME_FORMAT", "leap seconds are not accepted")

    text = value if match.group("offset") != "Z" else value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        _fail("E_TIME_FORMAT", "timestamp is not a valid instant")
    if not 1970 <= parsed.year <= 9999:
        _fail("E_TIME_FORMAT", "year outside [1970, 9999]")
    return parsed


def canonical_timestamp(value: Any) -> str:
    """Return the canonical UTC form ``%Y-%m-%dT%H:%M:%SZ`` of ``value``.

    Fractional seconds are **truncated, not rounded**: rounding could push an
    ``expires_at`` across its own boundary.
    """
    parsed = parse_timestamp(value).astimezone(timezone.utc)
    truncated = parsed.replace(microsecond=0)
    if not 1970 <= truncated.year <= 9999:
        _fail("E_TIME_FORMAT", "year outside [1970, 9999] after UTC conversion")
    return truncated.strftime("%Y-%m-%dT%H:%M:%SZ")
