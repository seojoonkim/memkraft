"""Tests for preview English claim extraction rules."""
from __future__ import annotations

import json
from pathlib import Path

from memkraft.claims import extract_claims

FIXTURE = Path(__file__).parent / "fixtures" / "claims_en.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_extract_claims_en_positive_fixture_matches_expected_fields():
    for case in _fixture()["positive"]:
        claims = extract_claims(case["text"])

        assert len(claims) == 1, case["id"]
        claim = claims[0]
        expected = case["expected"]
        assert claim["subject"] == expected["subject"]
        assert claim["predicate"] == expected["predicate"]
        assert claim["object_value"] == expected["object_value"]
        assert claim["source_quote"] == expected["source_quote"]
        assert claim["source_quote"] in case["text"]
        assert claim["confidence"] == 0.9

        if "valid_from" in expected:
            assert claim["valid_from"] == expected["valid_from"]
        else:
            assert "valid_from" not in claim


def test_extract_claims_en_negative_fixture_extracts_no_claims():
    for case in _fixture()["negative"]:
        assert extract_claims(case["text"]) == [], case["id"]


def test_extract_claims_uses_entity_hint_when_subject_is_implicit():
    assert extract_claims("prefers dark mode.", entity_hint="Alice") == [
        {
            "subject": "Alice",
            "predicate": "prefers",
            "object_value": "dark mode",
            "source_quote": "prefers dark mode",
            "confidence": 0.9,
        }
    ]


def test_extract_claims_returns_empty_list_for_non_string_or_unmatched_input():
    assert extract_claims(None) == []  # type: ignore[arg-type]
    assert extract_claims(123) == []  # type: ignore[arg-type]
    assert extract_claims("") == []
    assert extract_claims("This sentence does not contain a supported claim.") == []


def test_extract_claims_is_deterministic_for_same_input():
    text = "Simon prefers concise updates when busy."
    first = extract_claims(text)

    for _ in range(100):
        assert extract_claims(text) == first
