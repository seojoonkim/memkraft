"""MemKraft v0.9.0 — Incident Memory Layer tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from memkraft import MemKraft, __version__


def _mk(tmp_path) -> MemKraft:
    mk = MemKraft(str(tmp_path / "memory"))
    mk.init()
    return mk


class TestVersion:
    def test_version_is_090(self):
        assert __version__.startswith("0.9.")


class TestIncidentRecord:
    def test_basic_record_returns_id(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="gateway double start",
            symptoms=["Address already in use"],
            severity="high",
        )
        assert iid.startswith("inc-")
        assert "gateway-double-start" in iid

    def test_file_is_created(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="db timeout",
            symptoms=["connection timeout after 10s"],
        )
        p = mk.base_dir / "incidents" / f"{iid}.md"
        assert p.exists()
        body = p.read_text(encoding="utf-8")
        assert "connection timeout after 10s" in body
        assert "type: incident" in body

    def test_open_incident_defaults_to_core_tier(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="partial outage",
            symptoms=["5xx rate spike"],
        )
        inc = mk.incident_get(iid)
        assert inc["frontmatter"]["tier"] == "core"
        assert inc["frontmatter"]["status"] == "open"

    def test_resolved_on_creation_auto_archival(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="already fixed",
            symptoms=["was broken"],
            resolution="restarted the pod",
        )
        inc = mk.incident_get(iid)
        assert inc["frontmatter"]["status"] == "resolved"
        assert inc["frontmatter"]["tier"] == "archival"

    def test_empty_symptoms_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.incident_record(title="x", symptoms=[])
        with pytest.raises(ValueError):
            mk.incident_record(title="x", symptoms=None)

    def test_empty_title_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.incident_record(title="", symptoms=["a"])
        with pytest.raises(ValueError):
            mk.incident_record(title="   ", symptoms=["a"])

    def test_invalid_severity_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.incident_record(
                title="t", symptoms=["s"], severity="nuclear"
            )

    def test_duplicate_id_gets_suffix(self, tmp_path):
        mk = _mk(tmp_path)
        a = mk.incident_record(
            title="same", symptoms=["s"],
            detected_at="2026-04-20T10:00:00",
        )
        b = mk.incident_record(
            title="same", symptoms=["s"],
            detected_at="2026-04-20T11:00:00",
        )
        assert a != b
        assert b.endswith("-2")

    def test_affected_and_tags_stored(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="net outage",
            symptoms=["ping fail"],
            affected=["gateway", "dns"],
            tags=["prod", "p1"],
        )
        inc = mk.incident_get(iid)
        assert inc["frontmatter"]["affected"] == ["gateway", "dns"]
        assert inc["frontmatter"]["tags"] == ["prod", "p1"]

    def test_evidence_stored_as_section(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="t",
            symptoms=["s"],
            evidence=[
                {"type": "log", "path": "/var/log/x.log"},
                "plain string evidence",
            ],
        )
        inc = mk.incident_get(iid)
        ev = inc["sections"]["Evidence"]
        assert any("/var/log/x.log" in line for line in ev)
        assert any("plain string evidence" in line for line in ev)


class TestIncidentUpdate:
    def test_add_hypothesis(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(title="t", symptoms=["s"])
        mk.incident_update(iid, add_hypothesis=["H1: race condition"])
        inc = mk.incident_get(iid)
        hyp = inc["sections"]["Hypotheses"]
        assert any("H1: race condition" in line for line in hyp)

    def test_reject_hypothesis(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="t", symptoms=["s"],
            hypothesis=["H1: cron dup", "H2: pid stale"],
        )
        mk.incident_update(iid, reject_hypothesis=["H1: cron dup"])
        inc = mk.incident_get(iid)
        hyp = " ".join(inc["sections"]["Hypotheses"])
        assert "[rejected" in hyp
        assert "H1: cron dup" in hyp

    def test_confirm_hypothesis(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="t", symptoms=["s"],
            hypothesis=["H2: pid stale"],
        )
        mk.incident_update(iid, confirm_hypothesis=["H2: pid stale"])
        inc = mk.incident_get(iid)
        hyp = " ".join(inc["sections"]["Hypotheses"])
        assert "[confirmed" in hyp

    def test_resolution_auto_resolves(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(title="t", symptoms=["s"])
        assert mk.incident_get(iid)["frontmatter"]["status"] == "open"
        mk.incident_update(iid, resolution="rebooted")
        inc = mk.incident_get(iid)
        assert inc["frontmatter"]["status"] == "resolved"
        assert inc["frontmatter"]["resolved_at"] is not None
        assert inc["frontmatter"]["tier"] == "archival"

    def test_explicit_resolved_false_reopens(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="t", symptoms=["s"], resolution="done"
        )
        assert mk.incident_get(iid)["frontmatter"]["status"] == "resolved"
        mk.incident_update(iid, resolved=False)
        inc = mk.incident_get(iid)
        assert inc["frontmatter"]["status"] == "open"
        assert inc["frontmatter"]["resolved_at"] is None

    def test_add_evidence_appends(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="t", symptoms=["s"],
            evidence=[{"type": "log", "path": "/a.log"}],
        )
        mk.incident_update(iid, add_evidence=[{"type": "metric", "value": "99.9"}])
        inc = mk.incident_get(iid)
        ev = " ".join(inc["sections"]["Evidence"])
        assert "/a.log" in ev
        assert "99.9" in ev

    def test_add_tags_and_affected_dedupes(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="t", symptoms=["s"],
            affected=["gateway"], tags=["prod"],
        )
        mk.incident_update(iid, affected=["gateway", "dns"], tags=["prod", "p1"])
        inc = mk.incident_get(iid)
        assert inc["frontmatter"]["affected"] == ["gateway", "dns"]
        assert inc["frontmatter"]["tags"] == ["prod", "p1"]

    def test_severity_update(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(title="t", symptoms=["s"], severity="low")
        mk.incident_update(iid, severity="critical")
        assert mk.incident_get(iid)["frontmatter"]["severity"] == "critical"

    def test_update_unknown_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(FileNotFoundError):
            mk.incident_update("inc-does-not-exist", resolution="x")

    def test_update_invalid_severity_raises(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(title="t", symptoms=["s"])
        with pytest.raises(ValueError):
            mk.incident_update(iid, severity="super-mega")


class TestIncidentSearch:
    def test_search_returns_empty_when_none(self, tmp_path):
        mk = _mk(tmp_path)
        assert mk.incident_search() == []

    def test_search_by_query(self, tmp_path):
        mk = _mk(tmp_path)
        mk.incident_record(title="gateway stall", symptoms=["timeout"])
        mk.incident_record(title="db slow", symptoms=["slow query"])
        r = mk.incident_search(query="gateway")
        assert len(r) == 1
        assert "gateway" in r[0]["title"]

    def test_search_by_severity(self, tmp_path):
        mk = _mk(tmp_path)
        mk.incident_record(title="a", symptoms=["x"], severity="low")
        mk.incident_record(title="b", symptoms=["x"], severity="critical")
        crit = mk.incident_search(severity="critical")
        assert len(crit) == 1
        assert crit[0]["title"] == "b"

    def test_search_by_status(self, tmp_path):
        mk = _mk(tmp_path)
        mk.incident_record(title="a", symptoms=["x"])  # open
        mk.incident_record(title="b", symptoms=["x"], resolution="done")
        assert len(mk.incident_search(status="open")) == 1
        assert len(mk.incident_search(status="resolved")) == 1
        assert len(mk.incident_search(resolved=True)) == 1
        assert len(mk.incident_search(resolved=False)) == 1

    def test_search_by_affected(self, tmp_path):
        mk = _mk(tmp_path)
        mk.incident_record(title="a", symptoms=["x"], affected=["gateway"])
        mk.incident_record(title="b", symptoms=["x"], affected=["dns"])
        r = mk.incident_search(affected="gateway")
        assert len(r) == 1
        assert r[0]["title"] == "a"

    def test_search_by_timeframe(self, tmp_path):
        mk = _mk(tmp_path)
        mk.incident_record(title="old", symptoms=["x"], detected_at="2025-01-01T00:00:00")
        mk.incident_record(title="new", symptoms=["x"], detected_at="2026-04-01T00:00:00")
        r = mk.incident_search(timeframe=("2026-01-01", "2027-01-01"))
        assert len(r) == 1
        assert r[0]["title"] == "new"

    def test_search_sorted_newest_first(self, tmp_path):
        mk = _mk(tmp_path)
        mk.incident_record(title="older", symptoms=["x"], detected_at="2025-01-01T00:00:00")
        mk.incident_record(title="newer", symptoms=["x"], detected_at="2026-04-01T00:00:00")
        r = mk.incident_search()
        assert r[0]["title"] == "newer"
        assert r[1]["title"] == "older"

    def test_search_limit(self, tmp_path):
        mk = _mk(tmp_path)
        for i in range(5):
            mk.incident_record(
                title=f"inc-{i}", symptoms=["x"],
                detected_at=f"2026-04-{10+i:02d}T00:00:00",
            )
        assert len(mk.incident_search(limit=3)) == 3

    def test_search_invalid_severity_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.incident_search(severity="cosmic")

    def test_search_invalid_status_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.incident_search(status="maybe")
