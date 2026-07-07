"""Resolver dry-run rules for MemKraft 2.13.

Preview tier: deterministic verdict classification only.  This module never
writes to storage and never promotes memories.
"""

from __future__ import annotations

from typing import Any

REQUIRED_CLAIM_KEYS = ("subject", "predicate", "object_value", "confidence")
VERDICTS = (
    "PREFERENCE_CLAIM",
    "TOOL_USE_CLAIM",
    "LOCATION_CLAIM",
    "CHANGE_CLAIM",
    "LOW_CONFIDENCE_REVIEW",
    "MISSING_SOURCE_REJECT",
    "INSTRUCTION_REJECT",
    "CANDIDATE_REVIEW",
)


class ResolverClaimError(ValueError):
    """Structured resolver input error."""

    def __init__(self, kind: str, message: str, param: str):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.param = param


def resolver_dry_run(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preview: classify claims into deterministic verdicts without writes.

    Verdicts: PREFERENCE_CLAIM, TOOL_USE_CLAIM, LOCATION_CLAIM, CHANGE_CLAIM,
    LOW_CONFIDENCE_REVIEW, MISSING_SOURCE_REJECT, INSTRUCTION_REJECT, and
    CANDIDATE_REVIEW.  Only non-rejected high-confidence supported claims with
    a source_quote receive ``can_promote=True``.
    """
    outputs: list[dict[str, Any]] = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ResolverClaimError("invalid_claim", "claim must be a dict", f"claim[{index}]")
        _validate_claim(claim, index)
        verdict = _verdict_for(claim)
        can_promote = verdict in {
            "PREFERENCE_CLAIM",
            "TOOL_USE_CLAIM",
            "LOCATION_CLAIM",
            "CHANGE_CLAIM",
        }
        outputs.append(
            {
                "claim_index": index,
                "verdict": verdict,
                "can_promote": can_promote,
                "reason": _reason_for(verdict),
                "claim": dict(claim),
            }
        )
    return outputs


def _validate_claim(claim: dict[str, Any], index: int) -> None:
    missing = [key for key in REQUIRED_CLAIM_KEYS if key not in claim]
    if missing:
        raise ResolverClaimError(
            "invalid_claim",
            f"claim missing required keys: {', '.join(missing)}",
            f"claim[{index}]",
        )


def _verdict_for(claim: dict[str, Any]) -> str:
    source = str(claim.get("source_quote", "")).strip()
    subject = str(claim.get("subject", ""))
    predicate = str(claim.get("predicate", ""))
    try:
        confidence = float(claim.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    if not source:
        return "MISSING_SOURCE_REJECT"
    lowered = f"{subject} {source}".lower()
    if "ignore previous instructions" in lowered or "system override" in lowered:
        return "INSTRUCTION_REJECT"
    if confidence < 0.75:
        return "LOW_CONFIDENCE_REVIEW"
    if predicate == "prefers":
        return "PREFERENCE_CLAIM"
    if predicate == "uses":
        return "TOOL_USE_CLAIM"
    if predicate == "is_located":
        return "LOCATION_CLAIM"
    if predicate in {"changed", "updated", "corrected"}:
        return "CHANGE_CLAIM"
    return "CANDIDATE_REVIEW"


def _reason_for(verdict: str) -> str:
    return {
        "PREFERENCE_CLAIM": "supported preference claim",
        "TOOL_USE_CLAIM": "supported tool-use claim",
        "LOCATION_CLAIM": "supported location claim",
        "CHANGE_CLAIM": "supported change claim",
        "LOW_CONFIDENCE_REVIEW": "confidence below active threshold",
        "MISSING_SOURCE_REJECT": "source_quote is required for promotion",
        "INSTRUCTION_REJECT": "instruction-like claim is rejected",
        "CANDIDATE_REVIEW": "unsupported claim requires review",
    }[verdict]
