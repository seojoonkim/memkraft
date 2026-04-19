"""MemKraft v0.9.0 — RCA tests."""

from __future__ import annotations

import pytest

from memkraft import MemKraft


def _mk(tmp_path) -> MemKraft:
    mk = MemKraft(str(tmp_path / "memory"))
    mk.init()
    return mk


class TestRCA:
    def test_basic_report_shape(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="gateway double start",
            symptoms=["Address already in use"],
            hypothesis=["stale PID"],
        )
        rep = mk.incident_rca(iid)
        assert rep["incident_id"] == iid
        assert rep["strategy"] == "heuristic"
        assert isinstance(rep["hypotheses"], list)
        assert "suggested_runbooks" in rep
        assert "related_incidents" in rep

    def test_confirmed_hypothesis_scores_highest(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="t", symptoms=["s"],
            hypothesis=["confirmed one", "testing one", "bad one"],
        )
        mk.incident_update(iid, confirm_hypothesis=["confirmed one"])
        mk.incident_update(iid, reject_hypothesis=["bad one"])
        rep = mk.incident_rca(iid)
        # the top hypothesis should be the confirmed one
        assert rep["hypotheses"][0]["status"] == "confirmed"
        # confirmed > testing > rejected
        scores = [h["score"] for h in rep["hypotheses"]]
        assert scores == sorted(scores, reverse=True)

    def test_rejected_scores_low(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="t", symptoms=["s"],
            hypothesis=["H1"],
        )
        mk.incident_update(iid, reject_hypothesis=["H1"])
        rep = mk.incident_rca(iid)
        rejected = [h for h in rep["hypotheses"] if h["status"] == "rejected"]
        assert rejected
        assert rejected[0]["score"] < 0.2

    def test_suggested_runbooks_from_first_symptom(self, tmp_path):
        mk = _mk(tmp_path)
        mk.runbook_add(
            pattern="timeout after 10s",
            steps=["retry with backoff"],
            confidence=0.7,
        )
        iid = mk.incident_record(
            title="slow db",
            symptoms=["timeout after 10s", "unrelated thing"],
        )
        rep = mk.incident_rca(iid)
        assert len(rep["suggested_runbooks"]) >= 1
        assert "timeout" in rep["suggested_runbooks"][0]["pattern"]

    def test_rca_suggestions_do_not_bump_usage_count(self, tmp_path):
        """RCA is observation-only — should not touch runbook usage."""
        mk = _mk(tmp_path)
        rid = mk.runbook_add(pattern="p", steps=["a"], confidence=0.5)
        before = mk.runbook_get(rid)["frontmatter"]["usage_count"]
        iid = mk.incident_record(title="t", symptoms=["p"])
        mk.incident_rca(iid)
        after = mk.runbook_get(rid)["frontmatter"]["usage_count"]
        assert before == after

    def test_related_incidents_by_affected_overlap(self, tmp_path):
        mk = _mk(tmp_path)
        a = mk.incident_record(
            title="first outage",
            symptoms=["gateway down"],
            affected=["gateway"],
        )
        b = mk.incident_record(
            title="second outage",
            symptoms=["gateway latency"],
            affected=["gateway"],
        )
        rep = mk.incident_rca(b)
        assert len(rep["related_incidents"]) >= 1
        ids = [r["id"] for r in rep["related_incidents"]]
        assert a in ids

    def test_related_incidents_excluded_when_include_related_false(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="t", symptoms=["s"], affected=["x"],
        )
        mk.incident_record(title="u", symptoms=["s"], affected=["x"])
        rep = mk.incident_rca(iid, include_related=False)
        assert "related_incidents" not in rep

    def test_invalid_strategy_raises(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(title="t", symptoms=["s"])
        with pytest.raises(ValueError):
            mk.incident_rca(iid, strategy="llm")
        with pytest.raises(ValueError):
            mk.incident_rca(iid, strategy="both")

    def test_unknown_incident_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(FileNotFoundError):
            mk.incident_rca("inc-does-not-exist")

    def test_evidence_bonus_applies(self, tmp_path):
        mk = _mk(tmp_path)
        iid_no_ev = mk.incident_record(
            title="a", symptoms=["sym"],
            hypothesis=["sym"],
        )
        iid_ev = mk.incident_record(
            title="b", symptoms=["sym"],
            hypothesis=["sym"],
            evidence=[{"k": "v"}, {"k": "v2"}, {"k": "v3"}],
        )
        rep_no = mk.incident_rca(iid_no_ev)
        rep_ev = mk.incident_rca(iid_ev)
        score_no = rep_no["hypotheses"][0]["score"]
        score_ev = rep_ev["hypotheses"][0]["score"]
        assert score_ev >= score_no
