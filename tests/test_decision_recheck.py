"""Decision recheck loop: record the expected result + a recheck date, then
surface decisions whose recheck date passed without an observed outcome."""

from __future__ import annotations

from memkraft import MemKraft


def _mk(tmp_path) -> MemKraft:
    mk = MemKraft(str(tmp_path / "memory"))
    mk.init()
    return mk


def test_expected_and_recheck_are_stored(tmp_path):
    mk = _mk(tmp_path)
    did = mk.decision_record(
        "Ship wheel after merge",
        "fixes sat unshipped for days",
        "rebuild venv wheel",
        expected="unshipped fix lag <= 1 day",
        recheck_at="2026-11-10",
    )
    got = mk.decision_get(did)
    assert got["frontmatter"]["recheck_at"] == "2026-11-10"
    assert got["sections"]["Expected"] == ["unshipped fix lag <= 1 day"]


def test_decision_due_lists_only_overdue_unchecked(tmp_path):
    mk = _mk(tmp_path)
    overdue = mk.decision_record("A", "w", "h", expected="x", recheck_at="2026-11-01")
    mk.decision_record("B", "w", "h", expected="y", recheck_at="2026-12-01")
    mk.decision_record("C", "w", "h")  # no recheck date -> never due
    rejected = mk.decision_record("D", "w", "h", recheck_at="2026-10-01", status="rejected")

    due = mk.decision_due(as_of="2026-11-03")
    ids = [d["id"] for d in due]
    assert ids == [overdue]
    assert rejected not in ids
    assert due[0]["expected"] == "x"
    assert due[0]["days_overdue"] == 2


def test_outcome_after_recheck_clears_due_and_can_reschedule(tmp_path):
    mk = _mk(tmp_path)
    did = mk.decision_record("A", "w", "h", expected="x", recheck_at="2026-11-01")
    assert [d["id"] for d in mk.decision_due(as_of="2026-11-03")] == [did]

    mk.decision_update(did, outcome="measured: lag 0 days", checked_at="2026-11-03")
    assert mk.decision_due(as_of="2026-11-03") == []

    mk.decision_update(did, recheck_at="2026-11-20")
    assert mk.decision_due(as_of="2026-11-10") == []
    assert [d["id"] for d in mk.decision_due(as_of="2026-11-21")] == [did]
