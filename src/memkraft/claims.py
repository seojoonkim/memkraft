"""Preview-tier deterministic claim extraction helpers.

This module intentionally implements only the narrow MemKraft 2.13 S8 English
patterns. It uses stdlib regular expressions only; broader NLP, LLM, Korean, or
heuristic extraction belongs to later slices.
"""
from __future__ import annotations

import re
from typing import Any

_CONFIDENCE = 0.9
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

_SUBJECT = r"(?P<subject>[A-Z][A-Za-z0-9]*(?:[ ._/-]+[A-Za-z0-9][A-Za-z0-9._/-]*){0,5})"
_OBJECT = r"(?P<object>.+?)"
_FIELD = r"(?P<field>[A-Za-z0-9][A-Za-z0-9 ._/-]*?)"
_TO = r"(?P<to>.+?)"

_EXPLICIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("prefers", re.compile(rf"^(?P<quote>{_SUBJECT}\s+prefers\s+{_OBJECT})(?:[.?!]|$)")),
    ("uses", re.compile(rf"^(?P<quote>{_SUBJECT}\s+uses\s+{_OBJECT})(?:[.?!]|$)")),
    (
        "is_located",
        re.compile(rf"^(?P<quote>{_SUBJECT}\s+(?:is|are)\s+located\s+(?:at|in)\s+{_OBJECT})(?:[.?!]|$)"),
    ),
    (
        "changed",
        re.compile(
            rf"^(?P<quote>{_SUBJECT}\s+(?P<verb>changed|updated|corrected)\s+{_FIELD}\s+to\s+{_TO}"
            rf"(?:\s+on\s+\d{{4}}-\d{{2}}-\d{{2}})?)(?:[.?!]|$)"
        ),
    ),
)

_IMPLICIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("prefers", re.compile(rf"^(?P<quote>prefers\s+{_OBJECT})(?:[.?!]|$)")),
    ("uses", re.compile(rf"^(?P<quote>uses\s+{_OBJECT})(?:[.?!]|$)")),
    (
        "is_located",
        re.compile(rf"^(?P<quote>(?:is|are)\s+located\s+(?:at|in)\s+{_OBJECT})(?:[.?!]|$)"),
    ),
    (
        "changed",
        re.compile(
            rf"^(?P<quote>(?P<verb>changed|updated|corrected)\s+{_FIELD}\s+to\s+{_TO}"
            rf"(?:\s+on\s+\d{{4}}-\d{{2}}-\d{{2}})?)(?:[.?!]|$)"
        ),
    ),
)


def extract_claims(text: str, *, entity_hint: str | None = None) -> list[dict[str, Any]]:
    """Extract S8 preview English claims from ``text``.

    Supported patterns only:
    - ``X prefers Y``
    - ``X uses Y``
    - ``X is located at/in Y`` or ``X are located at/in Y``
    - ``X changed/updated/corrected Y to Z`` with optional ``on YYYY-MM-DD``

    Unsupported, malformed, non-string, or ambiguous inputs return an empty
    list rather than raising.
    """
    if not isinstance(text, str):
        return []
    stripped = text.strip()
    if not stripped or stripped.endswith("?"):
        return []

    for predicate, pattern in _EXPLICIT_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return [_claim_from_match(predicate, match)]

    hint = entity_hint.strip() if isinstance(entity_hint, str) else ""
    if hint:
        for predicate, pattern in _IMPLICIT_PATTERNS:
            match = pattern.match(stripped)
            if match:
                return [_claim_from_match(predicate, match, subject_override=hint)]
    return []


def _claim_from_match(
    predicate: str,
    match: re.Match[str],
    *,
    subject_override: str | None = None,
) -> dict[str, Any]:
    subject = subject_override or match.group("subject").strip()
    quote = match.group("quote").strip()

    if predicate == "changed":
        verb = match.group("verb")
        to_value = _strip_date_suffix(match.group("to").strip())
        claim: dict[str, Any] = {
            "subject": subject,
            "predicate": verb,
            "object_value": {"field": match.group("field").strip(), "to": to_value},
            "source_quote": quote,
            "confidence": _CONFIDENCE,
        }
        date_match = _DATE_RE.search(quote)
        if date_match:
            claim["valid_from"] = date_match.group(1)
        return claim

    return {
        "subject": subject,
        "predicate": predicate,
        "object_value": match.group("object").strip(),
        "source_quote": quote,
        "confidence": _CONFIDENCE,
    }


def _strip_date_suffix(value: str) -> str:
    return re.sub(r"\s+on\s+\d{4}-\d{2}-\d{2}$", "", value).strip()
