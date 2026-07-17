"""
LongMemEval 점수 계산기.

- exact_match: 정규화 후 완전 일치
- contains_match: 정답이 예측에 포함 (LongMemEval 표준 메트릭 근사)
- "i don't know" 처리: abstention 카테고리 별도 집계
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


ABSTENTION_MARKERS = ("i don't know", "i do not know", "not enough", "cannot determine", "cannot find")

_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}


def normalize(text) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = text.lower().strip()
    text = re.sub(r"[^\w\s가-힣]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_abstention(pred) -> bool:
    if pred is None:
        return False
    if not isinstance(pred, str):
        pred = str(pred)
    n = pred.lower()
    return any(m in n for m in ABSTENTION_MARKERS)


def exact_match(pred: str, gold: str) -> bool:
    return normalize(pred) == normalize(gold)


def contains_match(pred: str, gold: str) -> bool:
    ng = normalize(gold)
    np_ = normalize(pred)
    if not ng:
        return False
    return ng in np_


def canonicalize(text) -> str:
    """Normalize narrow answer-surface variants without semantic guessing."""
    if text is None:
        return ""
    value = str(text).lower().strip()
    value = re.sub(r"\b([ap])\s*\.\s*m\s*\.", r"\1m", value)
    value = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", r"\1", value)
    for word, digit in _NUMBER_WORDS.items():
        value = re.sub(rf"\b{word}\b", digit, value)
    value = re.sub(
        r"\b(\d+(?:\.\d+)?)\s+(gb|mb|tb|kb|kg|mg|km|cm|mm|hz|mhz|ghz)\b",
        r"\1\2",
        value,
    )
    value = re.sub(r"[^\w\s가-힣]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _acceptable_alternatives(gold: str) -> list[str]:
    """Extract explicitly allowed numeric answers from LongMemEval gold text."""
    text = str(gold or "")
    if "acceptable" not in text.lower():
        return []
    return re.findall(
        r"\b(?:\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
        r"\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b",
        text,
        flags=re.IGNORECASE,
    )


def canonical_match(pred: str, gold: str) -> bool:
    """Match deterministic formatting variants while preserving legacy metrics."""
    prediction = canonicalize(pred)
    expected = canonicalize(gold)

    def contains_complete_answer(answer: str) -> bool:
        return bool(answer) and re.search(
            rf"(?<!\w){re.escape(answer)}(?!\w)", prediction
        ) is not None

    if contains_complete_answer(expected):
        return True
    return any(
        contains_complete_answer(canonicalize(answer))
        for answer in _acceptable_alternatives(gold)
    )


def score_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_cat: dict[str, dict[str, int]] = defaultdict(
        lambda: {"em": 0, "contains": 0, "canonical": 0, "abst": 0, "total": 0}
    )
    total_em = total_contains = total_canonical = total_abst = 0
    errors = 0

    for r in results:
        if r.get("error"):
            errors += 1
            cat = r.get("question_type", "unknown")
            by_cat[cat]["total"] += 1
            continue
        cat = r.get("question_type", "unknown")
        pred = r.get("prediction", "")
        gold = r.get("answer", "")

        em = exact_match(pred, gold)
        cm = contains_match(pred, gold)
        canonical = canonical_match(pred, gold)
        ab = is_abstention(pred)

        by_cat[cat]["em"] += int(em)
        by_cat[cat]["contains"] += int(cm)
        by_cat[cat]["canonical"] += int(canonical)
        by_cat[cat]["abst"] += int(ab)
        by_cat[cat]["total"] += 1
        total_em += int(em)
        total_contains += int(cm)
        total_canonical += int(canonical)
        total_abst += int(ab)

    n = len(results) or 1
    return {
        "total": len(results),
        "errors": errors,
        "exact_match": total_em / n,
        "contains_match": total_contains / n,
        "canonical_match": total_canonical / n,
        "abstention_rate": total_abst / n,
        "by_category": {
            cat: {
                "em": v["em"] / v["total"] if v["total"] else 0.0,
                "contains": v["contains"] / v["total"] if v["total"] else 0.0,
                "canonical": v["canonical"] / v["total"] if v["total"] else 0.0,
                "abst": v["abst"] / v["total"] if v["total"] else 0.0,
                "total": v["total"],
            }
            for cat, v in by_cat.items()
        },
    }
