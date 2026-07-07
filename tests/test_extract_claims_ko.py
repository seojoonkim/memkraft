"""Tests for preview Korean claim extraction rules."""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from memkraft.claims import extract_claims

KO_FIXTURE = Path(__file__).parent / "fixtures" / "claims_ko.json"
EN_FIXTURE = Path(__file__).parent / "fixtures" / "claims_en.json"


def _fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_extract_claims_ko_positive_fixture_matches_expected_fields():
    for case in _fixture(KO_FIXTURE)["positive"]:
        claims = extract_claims(case["text"], entity_hint=case.get("entity_hint"))

        assert len(claims) == 1, case["id"]
        claim = claims[0]
        expected = case["expected"]
        assert claim["subject"] == expected["subject"]
        assert claim["predicate"] == expected["predicate"]
        assert claim["object_value"] == expected["object_value"]
        assert claim["source_quote"] == expected["source_quote"]
        assert claim["source_quote"] in unicodedata.normalize("NFC", case["text"])
        assert claim["confidence"] == 0.9


def test_extract_claims_ko_negative_fixture_extracts_no_claims():
    for case in _fixture(KO_FIXTURE)["negative"]:
        assert extract_claims(case["text"]) == [], case["id"]


def test_extract_claims_ko_requires_entity_hint_for_change_pattern():
    assert extract_claims("테마를 라이트 모드로 변경했다.") == []
    assert extract_claims("테마를 라이트 모드로 변경했다.", entity_hint="시몬")[0]["subject"] == "시몬"


def test_extract_claims_ko_nfc_normalizes_decomposed_hangul():
    text = "스킬을 검증 우선으로 변경했다."

    claims = extract_claims(text, entity_hint="사노")

    assert claims == [
        {
            "subject": "사노",
            "predicate": "changed",
            "object_value": {"field": "스킬", "to": "검증 우선"},
            "source_quote": "스킬을 검증 우선으로 변경",
            "confidence": 0.9,
        }
    ]


def test_extract_claims_ko_does_not_regress_english_fixture():
    for case in _fixture(EN_FIXTURE)["positive"]:
        assert extract_claims(case["text"]) != [], case["id"]
    for case in _fixture(EN_FIXTURE)["negative"]:
        assert extract_claims(case["text"]) == [], case["id"]


def test_extract_claims_ko_is_deterministic_for_same_input():
    text = "시몬은 정중한 문구를 선호한다."
    first = extract_claims(text)

    for _ in range(100):
        assert extract_claims(text) == first
