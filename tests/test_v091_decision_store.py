"""MemKraft v0.9.1 — Decision Layer tests."""

from __future__ import annotations

import pytest

from memkraft import MemKraft, __version__


def _mk(tmp_path) -> MemKraft:
    mk = MemKraft(str(tmp_path / "memory"))
    mk.init()
    return mk


class TestVersion:
    def test_version_is_091_or_newer(self):
        # Strip any PEP 440 pre/post/dev suffix (e.g. ``0.9.2a1`` → ``0.9.2``)
        # so alpha/beta releases still parse cleanly.
        import re

        raw_parts = __version__.split(".")[:3]
        parts = tuple(int(re.match(r"(\d+)", p).group(1)) for p in raw_parts)
        assert parts >= (0, 9, 1), f"expected >= 0.9.1, got {__version__}"


class TestDecisionRecord:
    def test_basic_returns_id(self, tmp_path):
        mk = _mk(tmp_path)
        did = mk.decision_record(
            what="Adopt MD-first memory",
            why="Plain text is debuggable and grep-friendly",
            how="Store decisions under memory/decisions/*.md",
        )
        assert did.startswith("dec-")
        assert "adopt-md-first-memory" in did

    def test_file_created_with_sections(self, tmp_path):
        mk = _mk(tmp_path)
        did = mk.decision_record(
            what="Use GoDaddy for domain checks",
            why="Cheaper and faster than whois",
            how="Call /v1/domains/available with API key",
        )
        p = mk.base_dir / "decisions" / f"{did}.md"
        assert p.exists()
        body = p.read_text(encoding="utf-8")
        assert "type: decision" in body
        assert "## What" in body
        assert "## Why" in body
        assert "## How" in body

    def test_accepted_default_status_and_core_tier(self, tmp_path):
        mk = _mk(tmp_path)
        did = mk.decision_record(what="x", why="y", how="z")
        d = mk.decision_get(did)
        assert d["frontmatter"]["status"] == "accepted"
        assert d["frontmatter"]["tier"] == "core"

    def test_superseded_status_auto_archival(self, tmp_path):
        mk = _mk(tmp_path)
        did = mk.decision_record(
            what="x", why="y", how="z", status="superseded"
        )
        d = mk.decision_get(did)
        assert d["frontmatter"]["tier"] == "archival"

    def test_empty_fields_raise(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.decision_record(what="", why="y", how="z")
        with pytest.raises(ValueError):
            mk.decision_record(what="x", why="  ", how="z")
        with pytest.raises(ValueError):
            mk.decision_record(what="x", why="y", how="")

    def test_invalid_status_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.decision_record(what="x", why="y", how="z", status="maybe")

    def test_id_collision_appends_suffix(self, tmp_path):
        mk = _mk(tmp_path)
        fixed = "2026-04-20T10:00:00"
        d1 = mk.decision_record(
            what="same title", why="y", how="z", decided_at=fixed
        )
        d2 = mk.decision_record(
            what="same title", why="y2", how="z2", decided_at=fixed
        )
        assert d1 != d2
        assert d2.endswith("-2")

    def test_tags_and_linked_incidents_persisted(self, tmp_path):
        mk = _mk(tmp_path)
        did = mk.decision_record(
            what="x",
            why="y",
            how="z",
            tags=["minions", "rca"],
            linked_incidents=["inc-2026-04-20-nonexistent"],
        )
        d = mk.decision_get(did)
        assert set(d["frontmatter"]["tags"]) == {"minions", "rca"}
        assert "inc-2026-04-20-nonexistent" in d["frontmatter"]["linked_incidents"]


class TestDecisionUpdate:
    def test_outcome_appended(self, tmp_path):
        mk = _mk(tmp_path)
        did = mk.decision_record(what="x", why="y", how="z")
        mk.decision_update(did, outcome="worked great")
        d = mk.decision_get(did)
        assert any("worked great" in line for line in d["sections"]["Outcome"])

    def test_multiple_outcomes_accumulate(self, tmp_path):
        mk = _mk(tmp_path)
        did = mk.decision_record(what="x", why="y", how="z")
        mk.decision_update(did, outcome="day 1 ok")
        mk.decision_update(did, outcome="day 7 still ok")
        d = mk.decision_get(did)
        assert len(d["sections"]["Outcome"]) == 2

    def test_status_change_to_superseded_archives(self, tmp_path):
        mk = _mk(tmp_path)
        did = mk.decision_record(what="x", why="y", how="z")
        mk.decision_update(did, status="superseded")
        d = mk.decision_get(did)
        assert d["frontmatter"]["status"] == "superseded"
        assert d["frontmatter"]["tier"] == "archival"

    def test_append_why_and_how(self, tmp_path):
        mk = _mk(tmp_path)
        did = mk.decision_record(what="x", why="initial why", how="initial how")
        mk.decision_update(did, append_why="additional context", append_how="phase 2 rollout")
        d = mk.decision_get(did)
        why_text = "\n".join(d["sections"]["Why"])
        how_text = "\n".join(d["sections"]["How"])
        assert "additional context" in why_text
        assert "phase 2 rollout" in how_text

    def test_add_tags_dedupe(self, tmp_path):
        mk = _mk(tmp_path)
        did = mk.decision_record(what="x", why="y", how="z", tags=["a"])
        mk.decision_update(did, tags=["a", "b"])
        d = mk.decision_get(did)
        assert sorted(d["frontmatter"]["tags"]) == ["a", "b"]

    def test_update_nonexistent_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(FileNotFoundError):
            mk.decision_update("dec-2026-01-01-ghost", outcome="nope")


class TestDecisionSearch:
    def test_substring_match(self, tmp_path):
        mk = _mk(tmp_path)
        mk.decision_record(what="Adopt GoDaddy API", why="speed", how="env vars")
        mk.decision_record(what="Use Minions", why="lightweight", how="pilot")
        results = mk.decision_search("minions")
        assert len(results) == 1
        assert "Minions" in results[0]["title"]

    def test_filter_by_status(self, tmp_path):
        mk = _mk(tmp_path)
        mk.decision_record(what="A", why="y", how="z", status="accepted")
        mk.decision_record(what="B", why="y", how="z", status="rejected")
        accepted = mk.decision_search(status="accepted")
        rejected = mk.decision_search(status="rejected")
        assert len(accepted) == 1 and accepted[0]["title"] == "A"
        assert len(rejected) == 1 and rejected[0]["title"] == "B"

    def test_filter_by_tag(self, tmp_path):
        mk = _mk(tmp_path)
        mk.decision_record(what="A", why="y", how="z", tags=["rca"])
        mk.decision_record(what="B", why="y", how="z", tags=["ui"])
        rca = mk.decision_search(tag="rca")
        assert len(rca) == 1 and rca[0]["title"] == "A"

    def test_filter_by_linked_incident(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="test inc", symptoms=["boom"], severity="low"
        )
        mk.decision_record(what="A", why="y", how="z", linked_incidents=[iid])
        mk.decision_record(what="B", why="y", how="z")
        hits = mk.decision_search(linked_incident=iid)
        assert len(hits) == 1 and hits[0]["title"] == "A"

    def test_search_sorts_newest_first(self, tmp_path):
        mk = _mk(tmp_path)
        mk.decision_record(
            what="Older", why="y", how="z", decided_at="2026-01-01T00:00:00"
        )
        mk.decision_record(
            what="Newer", why="y", how="z", decided_at="2026-04-20T00:00:00"
        )
        results = mk.decision_search()
        assert results[0]["title"] == "Newer"
        assert results[1]["title"] == "Older"

    def test_limit(self, tmp_path):
        mk = _mk(tmp_path)
        for i in range(5):
            mk.decision_record(
                what=f"Decision {i}",
                why="y",
                how="z",
                decided_at=f"2026-04-{20-i:02d}T00:00:00",
            )
        results = mk.decision_search(limit=3)
        assert len(results) == 3

    def test_invalid_status_filter_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.decision_search(status="not-a-status")

    def test_bad_timeframe_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.decision_search(timeframe="not a tuple")


class TestDecisionLink:
    def test_link_creates_bidirectional_ref(self, tmp_path):
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="inc for linking", symptoms=["err"], severity="low"
        )
        did = mk.decision_record(what="X", why="y", how="z")
        mk.decision_link(did, iid)

        d = mk.decision_get(did)
        assert iid in d["frontmatter"]["linked_incidents"]

        inc = mk.incident_get(iid)
        related_text = "\n".join(inc["sections"].get("Related", []))
        assert did in related_text

    def test_linking_nonexistent_incident_is_tolerated(self, tmp_path):
        """Link to a not-yet-created incident should not raise — best effort."""
        mk = _mk(tmp_path)
        did = mk.decision_record(what="X", why="y", how="z")
        mk.decision_link(did, "inc-2999-01-01-future")
        d = mk.decision_get(did)
        assert "inc-2999-01-01-future" in d["frontmatter"]["linked_incidents"]


class TestEvidenceFirst:
    def test_basic_runs_all_buckets(self, tmp_path):
        mk = _mk(tmp_path)
        # memory
        mk.track("Simon", entity_type="person", source="DM")
        # incident
        mk.incident_record(title="Simon gateway alert", symptoms=["401"])
        # decision
        mk.decision_record(what="Lock Simon account policy", why="security", how="audit")

        ef = mk.evidence_first("Simon", limit=10)
        assert ef["counts"]["decision"] >= 1
        assert ef["counts"]["incident"] >= 1
        assert ef["total_merged"] >= 2
        assert "elapsed_ms" in ef

    def test_empty_query_raises(self, tmp_path):
        mk = _mk(tmp_path)
        with pytest.raises(ValueError):
            mk.evidence_first("")
        with pytest.raises(ValueError):
            mk.evidence_first("   ")

    def test_results_tagged_with_source(self, tmp_path):
        mk = _mk(tmp_path)
        mk.decision_record(what="Source test", why="y", how="z")
        ef = mk.evidence_first("Source", limit=5)
        sources = {r.get("_source") for r in ef["results"]}
        assert "decision" in sources

    def test_limit_respected(self, tmp_path):
        mk = _mk(tmp_path)
        for i in range(5):
            mk.decision_record(what=f"Alpha decision {i}", why="y", how="z")
        ef = mk.evidence_first("Alpha", limit=3)
        assert ef["top_n"] <= 3


class TestBackwardCompat:
    def test_existing_incident_api_unchanged(self, tmp_path):
        """Decision layer must not break the v0.9.0 incident API."""
        mk = _mk(tmp_path)
        iid = mk.incident_record(
            title="backcompat test",
            symptoms=["s1"],
            severity="medium",
        )
        assert iid.startswith("inc-")
        inc = mk.incident_get(iid)
        assert inc["frontmatter"]["status"] == "open"

    def test_existing_search_api_unchanged(self, tmp_path):
        mk = _mk(tmp_path)
        mk.track("TestEntity", entity_type="person", source="DM")
        results = mk.search("TestEntity")
        assert len(results) >= 1
