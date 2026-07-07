"""Tests for resolver dry-run — MemKraft 2.13 S12."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.resolver import ResolverClaimError, resolver_dry_run
from benchmarks.gym.scenarios import registered_scenarios, run_scenario
from benchmarks.gym.gates import MIN_RESOLVER_VERDICT_ACCURACY


FIXTURE = Path(__file__).parent / "fixtures" / "resolver_cases.json"


def _fixture_cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_resolver_dry_run_matches_labeled_fixture_accuracy():
    cases = _fixture_cases()
    outputs = resolver_dry_run([case["claim"] for case in cases])

    correct = sum(
        out["verdict"] == case["expected_verdict"]
        for out, case in zip(outputs, cases)
    )
    assert correct / len(cases) >= MIN_RESOLVER_VERDICT_ACCURACY
    assert len({out["verdict"] for out in outputs}) == 8


def test_resolver_dry_run_is_deterministic():
    claims = [case["claim"] for case in _fixture_cases()[:16]]
    first = resolver_dry_run(claims)

    for _ in range(100):
        assert resolver_dry_run(claims) == first


def test_missing_source_quote_never_allows_active_promotion():
    missing_source_claims = [
        case["claim"]
        for case in _fixture_cases()
        if case["expected_verdict"] == "MISSING_SOURCE_REJECT"
    ]

    outputs = resolver_dry_run(missing_source_claims)

    assert outputs
    assert all(out["can_promote"] is False for out in outputs)


def test_resolver_dry_run_does_not_write_to_store(tmp_path: Path):
    before = _tree_hash(tmp_path)
    claims = [case["claim"] for case in _fixture_cases()[:8]]

    resolver_dry_run(claims)

    assert _tree_hash(tmp_path) == before


def test_invalid_claim_dict_raises_structured_error():
    with pytest.raises(ResolverClaimError) as excinfo:
        resolver_dry_run([{"subject": "Simon", "predicate": "prefers"}])

    assert excinfo.value.kind == "invalid_claim"
    assert excinfo.value.param == "claim[0]"


def test_remember_candidate_connects_extract_claims_before_resolver(tmp_path: Path):
    mk = MemKraft(str(tmp_path))

    mk.remember_candidate("Simon prefers concise updates.", session_id="s1", entity_hint="Simon")
    mk.remember_candidate("This is not a supported claim.", session_id="s1")

    candidates = mk.list_candidates(session_id="s1")
    assert len(candidates[0]["claims"]) == 1
    assert candidates[0]["claims"][0]["predicate"] == "prefers"
    assert candidates[1]["claims"] == []
    assert candidates[1]["review_state"] == "CANDIDATE_REVIEW"


def test_resolver_verdicts_gym_scenario_is_registered_and_passes():
    assert "resolver_verdicts" in registered_scenarios()

    payload = run_scenario("resolver_verdicts", sizes=[120], top_k=5)

    result = payload["results"][0]
    assert result["accuracy"] >= MIN_RESOLVER_VERDICT_ACCURACY
    assert result["determinism"] == 1.0
    assert result["missing_source_promotions"] == 0
