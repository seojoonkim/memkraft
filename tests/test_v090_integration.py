"""MemKraft v0.9.0 — Integration scenarios."""

from __future__ import annotations

from memkraft import MemKraft


def _mk(tmp_path) -> MemKraft:
    mk = MemKraft(str(tmp_path / "memory"))
    mk.init()
    return mk


class TestFullLifecycle:
    def test_incident_to_resolution_to_runbook_to_match(self, tmp_path):
        """Full happy path: detect -> diagnose -> resolve -> runbook -> match."""
        mk = _mk(tmp_path)

        # Day 1: detect
        iid = mk.incident_record(
            title="gateway ghost process",
            symptoms=["Address already in use", "pgrep gateway returns 2"],
            severity="high",
            affected=["gateway"],
            source="cron-log",
        )
        assert mk.incident_get(iid)["frontmatter"]["status"] == "open"
        assert mk.incident_get(iid)["frontmatter"]["tier"] == "core"

        # Day 1+1h: hypothesize
        mk.incident_update(iid, add_hypothesis=["stale PID file"])
        mk.incident_update(iid, add_hypothesis=["cron double run"])
        mk.incident_update(iid, reject_hypothesis=["cron double run"])
        mk.incident_update(iid, confirm_hypothesis=["stale PID file"])

        # Day 1+2h: resolve + extract runbook
        mk.incident_update(
            iid,
            resolution="add `kill -0 $PID` check before restart",
        )
        inc = mk.incident_get(iid)
        assert inc["frontmatter"]["status"] == "resolved"
        assert inc["frontmatter"]["tier"] == "archival"

        rid = mk.runbook_add(
            pattern="Address already in use",
            steps=[
                "pgrep -af gateway",
                "kill -0 $(cat gateway.pid) to verify aliveness",
                "rm pid file, restart guard",
            ],
            cause="stale PID without liveness check",
            source_incident_id=iid,
            confidence=0.8,
        )

        # Day 30: same symptom recurs — should match runbook
        matches = mk.runbook_match("Address already in use on gateway")
        assert len(matches) >= 1
        assert matches[0]["id"] == rid
        assert matches[0]["score"] > 0.4

    def test_multi_incident_search_filters_compose(self, tmp_path):
        mk = _mk(tmp_path)

        mk.incident_record(
            title="critical payment outage",
            symptoms=["500s on /pay"],
            severity="critical",
            affected=["payments"],
            detected_at="2026-04-10T09:00:00",
        )
        mk.incident_record(
            title="payment latency blip",
            symptoms=["p99 up"],
            severity="low",
            affected=["payments"],
            detected_at="2026-04-15T14:00:00",
        )
        mk.incident_record(
            title="unrelated cdn issue",
            symptoms=["404s from cdn"],
            severity="medium",
            affected=["cdn"],
            detected_at="2026-04-12T10:00:00",
            resolution="purged cache",
        )

        # open + payments only
        r = mk.incident_search(affected="payments", status="open")
        assert len(r) == 2

        # critical only
        r = mk.incident_search(severity="critical")
        assert len(r) == 1
        assert r[0]["title"] == "critical payment outage"

        # resolved only
        r = mk.incident_search(resolved=True)
        assert len(r) == 1
        assert r[0]["affected"] == ["cdn"]

        # timeframe
        r = mk.incident_search(timeframe=("2026-04-12", "2026-04-13"))
        assert len(r) == 1
        assert r[0]["title"] == "unrelated cdn issue"

    def test_rca_workflow_uses_runbook_and_related(self, tmp_path):
        mk = _mk(tmp_path)

        # Prior resolved incident with runbook
        old = mk.incident_record(
            title="earlier gateway storm",
            symptoms=["connection refused"],
            affected=["gateway"],
            resolution="restarted",
        )
        mk.runbook_add(
            pattern="connection refused",
            steps=["check port", "restart"],
            confidence=0.6,
            source_incident_id=old,
        )

        # New incident, same affected component
        new = mk.incident_record(
            title="gateway storm again",
            symptoms=["connection refused", "high retry rate"],
            affected=["gateway"],
            hypothesis=["same stale-lock bug"],
        )

        rep = mk.incident_rca(new)
        # should surface the runbook
        assert len(rep["suggested_runbooks"]) >= 1
        # should surface the related prior incident
        related_ids = [r["id"] for r in rep["related_incidents"]]
        assert old in related_ids

    def test_backward_compat_v080_apis_still_work(self, tmp_path):
        """v0.9 must not break v0.8 APIs (track/update/search/tier_set/fact_add)."""
        mk = _mk(tmp_path)

        # v0.8 entity API still works
        mk.track("Simon", entity_type="person", source="test")
        mk.update("Simon", "became CEO", source="test")
        res = mk.search("Simon")
        assert res is not None  # some truthy result

        # v0.8 fact_add still works
        mk.fact_add(
            "Simon", "role", "CEO",
            valid_from="2020-03-01",
        )
        hist = mk.fact_history("Simon", key="role")
        assert len(hist) >= 1

        # v0.9 addition doesn't interfere
        iid = mk.incident_record(title="t", symptoms=["s"])
        assert iid.startswith("inc-")

    def test_incident_affected_with_special_chars_roundtrip(self, tmp_path):
        """Frontmatter JSON-encoded list fields survive unicode + punctuation."""
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="유니코드 케이스",
            symptoms=["한글 증상 with punctuation: colons, brackets [x]"],
            affected=["gateway:prod", "dns/v2"],
            tags=["형-지시", "p1"],
        )
        inc = mk.incident_get(iid)
        assert inc["frontmatter"]["affected"] == ["gateway:prod", "dns/v2"]
        assert inc["frontmatter"]["tags"] == ["형-지시", "p1"]
        syms = " ".join(inc["sections"]["Symptoms"])
        assert "한글 증상" in syms
