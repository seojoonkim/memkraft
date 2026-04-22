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


def score_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_cat: dict[str, dict[str, int]] = defaultdict(lambda: {"em": 0, "contains": 0, "abst": 0, "total": 0})
    total_em = total_contains = total_abst = 0
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
        ab = is_abstention(pred)

        by_cat[cat]["em"] += int(em)
        by_cat[cat]["contains"] += int(cm)
        by_cat[cat]["abst"] += int(ab)
        by_cat[cat]["total"] += 1
        total_em += int(em)
        total_contains += int(cm)
        total_abst += int(ab)

    n = len(results) or 1
    return {
        "total": len(results),
        "errors": errors,
        "exact_match": total_em / n,
        "contains_match": total_contains / n,
        "abstention_rate": total_abst / n,
        "by_category": {
            cat: {
                "em": v["em"] / v["total"] if v["total"] else 0.0,
                "contains": v["contains"] / v["total"] if v["total"] else 0.0,
                "abst": v["abst"] / v["total"] if v["total"] else 0.0,
                "total": v["total"],
            }
            for cat, v in by_cat.items()
        },
    }
