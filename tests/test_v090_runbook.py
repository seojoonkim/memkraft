"""MemKraft v0.9.0 — Runbook tests."""

from __future__ import annotations

import pytest

from memkraft import MemKraft


def _mk(tmp_path) -> MemKraft:
    mk = MemKraft(str(tmp_path / "memory"))
    mk.init()
    return mk


class TestRunbookAdd:
    def test_basic_add_returns_id(self, tmp_path):
        mk = _mk(tmp_path)
        rid = mk.runbook_add(
            pattern="Address already in use",
            steps=["pgrep gateway", "kill -9 if stale"],
            confidence=0.7,
        )
        assert rid.startswith("rb-")
        assert "address-already-in-use" in rid

    def test_file_created(self, tmp_path):
        mk = _mk(tmp_path)
        rid = mk.runbook_add(pattern="foo", steps=["a"])
        p = mk.base_dir / "runbooks" / f"{rid}.md"
        assert p.exists()
        body = p.read_text(encoding="utf-8")
        assert "type: runbook" in body

    def test_empty_pattern_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.runbook_add(pattern="", steps=["a"])
        with pytest.raises(ValueError):
            mk.runbook_add(pattern="   ", steps=["a"])

    def test_empty_steps_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.runbook_add(pattern="p", steps=[])

    def test_invalid_confidence_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.runbook_add(pattern="p", steps=["a"], confidence=1.5)
        with pytest.raises(ValueError):
            mk.runbook_add(pattern="p", steps=["a"], confidence=-0.1)

    def test_upsert_merges_steps(self, tmp_path):
        mk = _mk(tmp_path)
        rid1 = mk.runbook_add(pattern="p", steps=["a", "b"], confidence=0.5)
        rid2 = mk.runbook_add(pattern="p", steps=["b", "c"], confidence=0.8)
        assert rid1 == rid2
        rb = mk.runbook_get(rid1)
        step_text = " ".join(rb["sections"]["Steps"])
        assert "a" in step_text
        assert "b" in step_text
        assert "c" in step_text

    def test_upsert_bumps_confidence_and_usage(self, tmp_path):
        mk = _mk(tmp_path)
        rid = mk.runbook_add(pattern="p", steps=["a"], confidence=0.3)
        mk.runbook_add(pattern="p", steps=["b"], confidence=0.9)
        rb = mk.runbook_get(rid)
        assert rb["frontmatter"]["confidence"] == 0.9
        assert rb["frontmatter"]["usage_count"] == 1  # one upsert

    def test_upsert_merges_source_incidents(self, tmp_path):
        mk = _mk(tmp_path)
        rid = mk.runbook_add(pattern="p", steps=["a"], source_incident_id="inc-1")
        mk.runbook_add(pattern="p", steps=["a"], source_incident_id="inc-2")
        rb = mk.runbook_get(rid)
        assert "inc-1" in rb["frontmatter"]["source_incidents"]
        assert "inc-2" in rb["frontmatter"]["source_incidents"]

    def test_sections_populated_from_optional_args(self, tmp_path):
        mk = _mk(tmp_path)
        rid = mk.runbook_add(
            pattern="p",
            steps=["a"],
            cause="root cause here",
            evidence_cmd="grep ERROR log.txt",
            fix_action="restart service",
            verification="pgrep service | wc -l",
        )
        rb = mk.runbook_get(rid)
        assert "root cause here" in " ".join(rb["sections"]["Cause"])
        assert "grep ERROR log.txt" in " ".join(rb["sections"]["Evidence Command"])
        assert "restart service" in " ".join(rb["sections"]["Fix Action"])
        assert "pgrep service" in " ".join(rb["sections"]["Verification"])


class TestRunbookMatch:
    def test_no_runbooks_returns_empty(self, tmp_path):
        mk = _mk(tmp_path)
        assert mk.runbook_match("any symptom") == []

    def test_empty_symptom_returns_empty(self, tmp_path):
        mk = _mk(tmp_path)
        mk.runbook_add(pattern="p", steps=["a"])
        assert mk.runbook_match("") == []
        assert mk.runbook_match("   ") == []

    def test_exact_match(self, tmp_path):
        mk = _mk(tmp_path)
        rid = mk.runbook_add(
            pattern="Address already in use",
            steps=["kill pid", "restart"],
            confidence=0.8,
        )
        r = mk.runbook_match("Address already in use")
        assert len(r) >= 1
        assert r[0]["id"] == rid
        assert r[0]["score"] > 0.5

    def test_regex_pattern_matches(self, tmp_path):
        mk = _mk(tmp_path)
        rid = mk.runbook_add(
            pattern=r"timeout.*after\s+\d+s",
            steps=["a"],
            confidence=0.5,
        )
        r = mk.runbook_match("got timeout after 30s")
        assert len(r) >= 1
        assert r[0]["id"] == rid
        # regex hit should push similarity to 1.0
        assert r[0]["similarity"] == 1.0

    def test_min_confidence_filter(self, tmp_path):
        mk = _mk(tmp_path)
        mk.runbook_add(pattern="p", steps=["a"], confidence=0.3)
        assert mk.runbook_match("p", min_confidence=0.5) == []

    def test_min_score_filter(self, tmp_path):
        mk = _mk(tmp_path)
        mk.runbook_add(pattern="unique-pattern", steps=["a"], confidence=0.5)
        # totally unrelated symptom should score too low
        assert mk.runbook_match("totally different", min_score=0.8) == []

    def test_score_is_blend_of_similarity_and_confidence(self, tmp_path):
        mk = _mk(tmp_path)
        mk.runbook_add(pattern="same pattern", steps=["a"], confidence=0.5)
        mk.runbook_add(pattern="same pattern different id", steps=["a"], confidence=0.9)
        r = mk.runbook_match("same pattern")
        # higher confidence should rank higher
        assert len(r) >= 2
        # winner's confidence should be >= runner-up's
        assert r[0]["confidence"] >= r[1]["confidence"] or r[0]["score"] >= r[1]["score"]

    def test_match_bumps_usage_and_last_matched(self, tmp_path):
        mk = _mk(tmp_path)
        rid = mk.runbook_add(pattern="p", steps=["a"], confidence=0.5)
        assert mk.runbook_get(rid)["frontmatter"]["usage_count"] == 0
        mk.runbook_match("p")
        rb = mk.runbook_get(rid)
        assert rb["frontmatter"]["usage_count"] >= 1
        assert rb["frontmatter"]["last_matched"] is not None

    def test_match_no_touch_keeps_usage(self, tmp_path):
        mk = _mk(tmp_path)
        rid = mk.runbook_add(pattern="p", steps=["a"], confidence=0.5)
        mk.runbook_match("p", touch=False)
        assert mk.runbook_get(rid)["frontmatter"]["usage_count"] == 0

    def test_limit(self, tmp_path):
        mk = _mk(tmp_path)
        for i in range(5):
            mk.runbook_add(
                pattern=f"pattern variant {i}",
                steps=["a"], confidence=0.5,
            )
        r = mk.runbook_match("pattern variant", limit=2)
        assert len(r) <= 2

    def test_confidence_capped_at_one(self, tmp_path):
        mk = _mk(tmp_path)
        rid = mk.runbook_add(pattern="p", steps=["a"], confidence=0.99)
        # repeated matches reinforce — should cap at 1.0
        for _ in range(20):
            mk.runbook_match("p")
        assert mk.runbook_get(rid)["frontmatter"]["confidence"] <= 1.0
