#!/usr/bin/env python3
"""Tests for MemKraft v0.4.0 Debug Hypothesis Tracking features.

Covers:
1. Debug session lifecycle (start/end)
2. Hypothesis CRUD (create/read/reject/confirm)
3. Evidence recording and retrieval
4. Full debug flow (OBSERVE -> HYPOTHESIZE -> EXPERIMENT -> CONCLUDE)
5. Auto-switch detection (2 failures -> switch)
6. Past session search
7. Rejected hypothesis search (anti-pattern detection)
8. CLI debug subcommands
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from memkraft.core import MemKraft


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def mk(tmp_path):
    """Create a MemKraft instance with a temporary directory."""
    mc = MemKraft(base_dir=str(tmp_path / "memory"))
    mc.init()
    return mc


@pytest.fixture
def mk_with_session(mk):
    """MemKraft instance with an active debug session."""
    result = mk.start_debug("App crashes on login")
    return mk, result["bug_id"]


@pytest.fixture
def mk_with_hypothesis(mk_with_session):
    """MemKraft instance with a debug session and one hypothesis."""
    mk, bug_id = mk_with_session
    mk.log_hypothesis(bug_id, "Null pointer in auth module")
    return mk, bug_id


# ── 1. Debug Session Start ───────────────────────────────────

class TestDebugStart:
    def test_start_creates_file(self, mk):
        result = mk.start_debug("TypeError in parser")
        assert "bug_id" in result
        assert result["bug_id"].startswith("DEBUG-")
        filepath = Path(result["file"])
        assert filepath.exists()

    def test_start_returns_observe_status(self, mk):
        result = mk.start_debug("Crash on startup")
        assert result["status"] == "OBSERVE"

    def test_start_file_contains_description(self, mk):
        result = mk.start_debug("Memory leak in worker")
        content = Path(result["file"]).read_text()
        assert "Memory leak in worker" in content

    def test_start_file_has_all_sections(self, mk):
        result = mk.start_debug("Test bug")
        content = Path(result["file"]).read_text()
        for section in ["## Observation", "## Hypotheses", "## Evidence Log", "## Conclusion", "## Timeline"]:
            assert section in content

    def test_start_file_status_is_observe(self, mk):
        result = mk.start_debug("Test bug")
        content = Path(result["file"]).read_text()
        assert "**Status:** OBSERVE" in content

    def test_start_creates_debug_dir(self, mk):
        mk.start_debug("Bug")
        assert mk.debug_dir.exists()

    def test_start_multiple_sessions(self, mk):
        r1 = mk.start_debug("Bug 1")
        time.sleep(1)  # ensure different timestamp
        r2 = mk.start_debug("Bug 2")
        assert r1["bug_id"] != r2["bug_id"]


# ── 2. Hypothesis CRUD ───────────────────────────────────────

class TestHypothesisCRUD:
    def test_log_hypothesis_returns_id(self, mk_with_session):
        mk, bug_id = mk_with_session
        result = mk.log_hypothesis(bug_id, "Bad regex pattern")
        assert result["hypothesis_id"] == "H1"
        assert result["status"] == "testing"

    def test_log_multiple_hypotheses(self, mk_with_session):
        mk, bug_id = mk_with_session
        r1 = mk.log_hypothesis(bug_id, "Hypothesis A")
        r2 = mk.log_hypothesis(bug_id, "Hypothesis B")
        assert r1["hypothesis_id"] == "H1"
        assert r2["hypothesis_id"] == "H2"

    def test_log_hypothesis_with_initial_evidence(self, mk_with_session):
        mk, bug_id = mk_with_session
        result = mk.log_hypothesis(bug_id, "Race condition", evidence="Thread dump shows deadlock")
        assert result["hypothesis_id"] == "H1"
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "Thread dump shows deadlock" in content

    def test_get_hypotheses_empty(self, mk_with_session):
        mk, bug_id = mk_with_session
        assert mk.get_hypotheses(bug_id) == []

    def test_get_hypotheses_returns_list(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        hyps = mk.get_hypotheses(bug_id)
        assert len(hyps) == 1
        assert hyps[0]["hypothesis_id"] == "H1"
        assert hyps[0]["status"] == "testing"
        assert "Null pointer" in hyps[0]["hypothesis"]

    def test_get_hypotheses_multiple(self, mk_with_session):
        mk, bug_id = mk_with_session
        mk.log_hypothesis(bug_id, "Theory A")
        mk.log_hypothesis(bug_id, "Theory B")
        mk.log_hypothesis(bug_id, "Theory C")
        hyps = mk.get_hypotheses(bug_id)
        assert len(hyps) == 3

    def test_invalid_hypothesis_status(self, mk_with_session):
        mk, bug_id = mk_with_session
        result = mk.log_hypothesis(bug_id, "Bad", status="invalid")
        assert result == {}

    def test_hypothesis_updates_status_to_hypothesize(self, mk_with_session):
        mk, bug_id = mk_with_session
        mk.log_hypothesis(bug_id, "Test")
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "**Status:** HYPOTHESIZE" in content

    def test_hypothesis_nonexistent_session(self, mk):
        result = mk.log_hypothesis("DEBUG-FAKE-000000", "Test")
        assert result == {}


# ── 3. Reject Hypothesis ─────────────────────────────────────

class TestRejectHypothesis:
    def test_reject_with_reason(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        result = mk.reject_hypothesis(bug_id, "H1", reason="Stack trace shows different module")
        assert result["status"] == "rejected"
        assert result["hypothesis_id"] == "H1"

    def test_reject_updates_file(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.reject_hypothesis(bug_id, "H1", reason="Wrong")
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "REJECTED" in content

    def test_reject_without_reason(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        result = mk.reject_hypothesis(bug_id, "H1")
        assert result["status"] == "rejected"

    def test_reject_nonexistent_hypothesis(self, mk_with_session):
        mk, bug_id = mk_with_session
        result = mk.reject_hypothesis(bug_id, "H99")
        assert result == {}

    def test_reject_already_rejected(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.reject_hypothesis(bug_id, "H1", reason="First rejection")
        # Trying to reject again should fail (not in TESTING state)
        result = mk.reject_hypothesis(bug_id, "H1", reason="Second rejection")
        assert result == {}

    def test_reject_preserves_hypothesis_text(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.reject_hypothesis(bug_id, "H1", reason="Disproven")
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "Null pointer in auth module" in content


# ── 4. Confirm Hypothesis ────────────────────────────────────

class TestConfirmHypothesis:
    def test_confirm_returns_confirmed(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        result = mk.confirm_hypothesis(bug_id, "H1")
        assert result["status"] == "confirmed"
        assert result["hypothesis_id"] == "H1"

    def test_confirm_updates_file(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.confirm_hypothesis(bug_id, "H1")
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "CONFIRMED" in content

    def test_confirm_nonexistent(self, mk_with_session):
        mk, bug_id = mk_with_session
        result = mk.confirm_hypothesis(bug_id, "H99")
        assert result == {}

    def test_confirm_rejected_hypothesis_fails(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.reject_hypothesis(bug_id, "H1")
        result = mk.confirm_hypothesis(bug_id, "H1")
        assert result == {}

    def test_confirm_includes_hypothesis_text(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        result = mk.confirm_hypothesis(bug_id, "H1")
        assert "Null pointer" in result.get("hypothesis", "")


# ── 5. Evidence Recording ────────────────────────────────────

class TestEvidence:
    def test_log_evidence_supports(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        result = mk.log_evidence(bug_id, "H1", "Stack trace points to line 42", result="supports")
        assert result["result"] == "supports"
        assert result["hypothesis_id"] == "H1"

    def test_log_evidence_contradicts(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        result = mk.log_evidence(bug_id, "H1", "Variable is initialized", result="contradicts")
        assert result["result"] == "contradicts"

    def test_log_evidence_neutral(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        result = mk.log_evidence(bug_id, "H1", "Unrelated log entry", result="neutral")
        assert result["result"] == "neutral"

    def test_log_evidence_invalid_result(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        result = mk.log_evidence(bug_id, "H1", "Test", result="maybe")
        assert result == {}

    def test_get_evidence_empty(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        evidence = mk.get_evidence(bug_id)
        assert evidence == []

    def test_get_evidence_returns_entries(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.log_evidence(bug_id, "H1", "Found error in log", result="supports")
        mk.log_evidence(bug_id, "H1", "Config is correct", result="contradicts")
        evidence = mk.get_evidence(bug_id)
        assert len(evidence) == 2

    def test_get_evidence_filtered_by_hypothesis(self, mk_with_session):
        mk, bug_id = mk_with_session
        mk.log_hypothesis(bug_id, "Hypothesis A")
        mk.log_hypothesis(bug_id, "Hypothesis B")
        mk.log_evidence(bug_id, "H1", "Evidence for A", result="supports")
        mk.log_evidence(bug_id, "H2", "Evidence for B", result="supports")
        h1_evidence = mk.get_evidence(bug_id, hypothesis_id="H1")
        assert len(h1_evidence) == 1
        assert "Evidence for A" in h1_evidence[0]["evidence"]

    def test_evidence_updates_status_to_experiment(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.log_evidence(bug_id, "H1", "Test evidence", result="supports")
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "**Status:** EXPERIMENT" in content

    def test_evidence_written_to_file(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.log_evidence(bug_id, "H1", "Segfault at 0xDEAD", result="supports")
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "Segfault at 0xDEAD" in content


# ── 6. End Debug Session ─────────────────────────────────────

class TestEndDebug:
    def test_end_session(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.confirm_hypothesis(bug_id, "H1")
        result = mk.end_debug(bug_id, "Fixed null check in auth.py")
        assert result["status"] == "CONCLUDE"
        assert result["resolution"] == "Fixed null check in auth.py"

    def test_end_updates_file_status(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.end_debug(bug_id, "Resolved")
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "**Status:** CONCLUDE" in content

    def test_end_writes_resolution(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.end_debug(bug_id, "Applied patch XYZ")
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "Applied patch XYZ" in content

    def test_end_counts_hypotheses(self, mk_with_session):
        mk, bug_id = mk_with_session
        mk.log_hypothesis(bug_id, "Wrong A")
        mk.reject_hypothesis(bug_id, "H1", reason="Nope")
        mk.log_hypothesis(bug_id, "Right B")
        mk.confirm_hypothesis(bug_id, "H2")
        result = mk.end_debug(bug_id, "Fixed via B")
        assert result["hypotheses_total"] == 2
        assert result["hypotheses_confirmed"] == 1
        assert result["hypotheses_rejected"] == 1

    def test_end_nonexistent_session(self, mk):
        result = mk.end_debug("DEBUG-FAKE", "test")
        assert result == {}


# ── 7. Full Debug Flow ───────────────────────────────────────

class TestFullDebugFlow:
    def test_complete_flow(self, mk):
        """Full flow: start -> hypothesis -> evidence -> reject -> new hypothesis -> confirm -> end"""
        # OBSERVE
        session = mk.start_debug("API returns 500 on POST /users")
        bug_id = session["bug_id"]
        assert session["status"] == "OBSERVE"

        # HYPOTHESIZE (first attempt)
        h1 = mk.log_hypothesis(bug_id, "Database connection timeout")
        assert h1["hypothesis_id"] == "H1"

        # EXPERIMENT (gather evidence)
        e1 = mk.log_evidence(bug_id, "H1", "DB connection pool healthy", result="contradicts")
        assert e1["result"] == "contradicts"

        # Reject H1
        r1 = mk.reject_hypothesis(bug_id, "H1", reason="DB is fine, connection pool healthy")
        assert r1["status"] == "rejected"

        # HYPOTHESIZE (second attempt)
        h2 = mk.log_hypothesis(bug_id, "Request validation fails on empty body")
        assert h2["hypothesis_id"] == "H2"

        # EXPERIMENT (more evidence)
        e2 = mk.log_evidence(bug_id, "H2", "Empty POST body triggers 500", result="supports")
        e3 = mk.log_evidence(bug_id, "H2", "Non-empty POST succeeds", result="supports")

        # Confirm H2
        c = mk.confirm_hypothesis(bug_id, "H2")
        assert c["status"] == "confirmed"

        # CONCLUDE
        end = mk.end_debug(bug_id, "Added request body validation middleware")
        assert end["status"] == "CONCLUDE"
        assert end["hypotheses_confirmed"] == 1
        assert end["hypotheses_rejected"] == 1

        # Verify file integrity
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "**Status:** CONCLUDE" in content
        assert "Database connection timeout" in content
        assert "Request validation fails" in content
        assert "Added request body validation middleware" in content


# ── 8. Auto-Switch Detection (2 Failures) ────────────────────

class TestAutoSwitchDetection:
    def test_two_rejections_trigger_warning(self, mk_with_session):
        mk, bug_id = mk_with_session
        mk.log_hypothesis(bug_id, "Theory A")
        mk.reject_hypothesis(bug_id, "H1", reason="Wrong")
        mk.log_hypothesis(bug_id, "Theory B")
        result = mk.reject_hypothesis(bug_id, "H2", reason="Also wrong")
        assert result["total_rejected"] == 2

    def test_two_rejections_in_timeline(self, mk_with_session):
        mk, bug_id = mk_with_session
        mk.log_hypothesis(bug_id, "A")
        mk.reject_hypothesis(bug_id, "H1", reason="no")
        mk.log_hypothesis(bug_id, "B")
        mk.reject_hypothesis(bug_id, "H2", reason="no")
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "AUTO-SWITCH TRIGGER" in content

    def test_four_rejections_trigger_again(self, mk_with_session):
        mk, bug_id = mk_with_session
        for i in range(4):
            mk.log_hypothesis(bug_id, f"Theory {i+1}")
            mk.reject_hypothesis(bug_id, f"H{i+1}", reason=f"Wrong {i+1}")
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        # Should have multiple AUTO-SWITCH triggers
        assert content.count("AUTO-SWITCH TRIGGER") >= 2

    def test_hypothesis_warning_message_on_add(self, mk_with_session, capsys):
        mk, bug_id = mk_with_session
        mk.log_hypothesis(bug_id, "A")
        mk.reject_hypothesis(bug_id, "H1")
        mk.log_hypothesis(bug_id, "B")
        mk.reject_hypothesis(bug_id, "H2")
        # Third hypothesis should show warning about rejected count
        mk.log_hypothesis(bug_id, "C")
        # The warning is embedded in the file when count is even
        filepath = mk._get_debug_file(bug_id)
        content = filepath.read_text()
        assert "rejected" in content.lower()


# ── 9. Debug History ─────────────────────────────────────────

class TestDebugHistory:
    def test_history_empty(self, mk):
        sessions = mk.debug_history()
        assert sessions == []

    def test_history_returns_sessions(self, mk):
        mk.start_debug("Bug 1")
        time.sleep(1)
        mk.start_debug("Bug 2")
        sessions = mk.debug_history()
        assert len(sessions) == 2

    def test_history_limit(self, mk):
        for i in range(5):
            mk.start_debug(f"Bug {i}")
            time.sleep(0.1)
        sessions = mk.debug_history(limit=3)
        assert len(sessions) == 3

    def test_history_shows_concluded(self, mk):
        r = mk.start_debug("Bug to fix")
        mk.log_hypothesis(r["bug_id"], "The fix")
        mk.confirm_hypothesis(r["bug_id"], "H1")
        mk.end_debug(r["bug_id"], "Fixed it")
        sessions = mk.debug_history()
        assert sessions[0]["status"] == "CONCLUDE"


# ── 10. Search Debug Sessions ────────────────────────────────

class TestSearchDebugSessions:
    def test_search_by_description(self, mk):
        mk.start_debug("Login page crashes on Safari")
        mk.start_debug("Payment API timeout")
        results = mk.search_debug_sessions("Safari")
        assert len(results) == 1
        assert "Safari" in results[0]["description"]

    def test_search_by_resolution(self, mk):
        r = mk.start_debug("Memory leak")
        mk.log_hypothesis(r["bug_id"], "Unclosed connection")
        mk.confirm_hypothesis(r["bug_id"], "H1")
        mk.end_debug(r["bug_id"], "Fixed connection pooling")
        results = mk.search_debug_sessions("connection pooling")
        assert len(results) == 1

    def test_search_no_results(self, mk):
        mk.start_debug("Some bug")
        results = mk.search_debug_sessions("nonexistent_query_xyz")
        assert results == []

    def test_search_empty_debug_dir(self, mk):
        results = mk.search_debug_sessions("anything")
        assert results == []


# ── 11. Search Rejected Hypotheses ────────────────────────────

class TestSearchRejectedHypotheses:
    def test_search_finds_rejected(self, mk):
        r = mk.start_debug("Bug A")
        mk.log_hypothesis(r["bug_id"], "Regex causes backtracking")
        mk.reject_hypothesis(r["bug_id"], "H1", reason="Regex is O(n)")
        results = mk.search_rejected_hypotheses("regex")
        assert len(results) >= 1
        assert results[0]["status"] == "rejected"

    def test_search_rejected_by_reason(self, mk):
        r = mk.start_debug("Bug B")
        mk.log_hypothesis(r["bug_id"], "Cache invalidation issue")
        mk.reject_hypothesis(r["bug_id"], "H1", reason="Cache TTL is correct, not the issue")
        results = mk.search_rejected_hypotheses("cache")
        assert len(results) >= 1

    def test_search_rejected_no_match(self, mk):
        r = mk.start_debug("Bug C")
        mk.log_hypothesis(r["bug_id"], "Thread deadlock")
        mk.reject_hypothesis(r["bug_id"], "H1", reason="No deadlock")
        results = mk.search_rejected_hypotheses("completely_unrelated_xyz")
        assert results == []

    def test_search_rejected_across_sessions(self, mk):
        r1 = mk.start_debug("Bug 1")
        mk.log_hypothesis(r1["bug_id"], "Network timeout")
        mk.reject_hypothesis(r1["bug_id"], "H1", reason="Network is stable")

        time.sleep(1)
        r2 = mk.start_debug("Bug 2")
        mk.log_hypothesis(r2["bug_id"], "Network packet loss")
        mk.reject_hypothesis(r2["bug_id"], "H1", reason="No packet loss detected")

        results = mk.search_rejected_hypotheses("network")
        assert len(results) == 2


# ── 12. Debug Session Status ─────────────────────────────────

class TestDebugStatus:
    def test_status_observe(self, mk_with_session):
        mk, bug_id = mk_with_session
        status = mk.get_debug_status(bug_id)
        assert status["status"] == "OBSERVE"
        assert status["hypotheses_total"] == 0

    def test_status_after_hypothesis(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        status = mk.get_debug_status(bug_id)
        assert status["status"] == "HYPOTHESIZE"
        assert status["hypotheses_testing"] == 1
        assert status["current_hypothesis"] == "H1"

    def test_status_after_evidence(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        mk.log_evidence(bug_id, "H1", "Test", result="supports")
        status = mk.get_debug_status(bug_id)
        assert status["status"] == "EXPERIMENT"
        assert status["evidence_count"] == 1

    def test_status_nonexistent(self, mk):
        status = mk.get_debug_status("DEBUG-FAKE")
        assert status == {}


# ── 13. Helper Method Tests ──────────────────────────────────

class TestDebugHelpers:
    def test_get_debug_file_exists(self, mk_with_session):
        mk, bug_id = mk_with_session
        filepath = mk._get_debug_file(bug_id)
        assert filepath is not None
        assert filepath.exists()

    def test_get_debug_file_nonexistent(self, mk):
        assert mk._get_debug_file("DEBUG-NONEXISTENT") is None

    def test_update_debug_status(self, mk):
        content = "**Status:** OBSERVE\nSome other text"
        updated = mk._update_debug_status(content, "HYPOTHESIZE")
        assert "**Status:** HYPOTHESIZE" in updated
        assert "**Status:** OBSERVE" not in updated

    def test_append_debug_timeline(self, mk):
        content = "## Timeline\n- **2026-04-13 12:00** | Started\n"
        updated = mk._append_debug_timeline(content, "New event")
        assert "New event" in updated


# ── 14. Edge Cases ────────────────────────────────────────────

class TestEdgeCases:
    def test_long_description(self, mk):
        desc = "A" * 1000
        result = mk.start_debug(desc)
        assert result["bug_id"].startswith("DEBUG-")

    def test_special_characters_in_hypothesis(self, mk_with_session):
        mk, bug_id = mk_with_session
        result = mk.log_hypothesis(bug_id, "Bug in path: /api/v1/users?id=123&name=test")
        assert result["hypothesis_id"] == "H1"

    def test_unicode_in_description(self, mk):
        result = mk.start_debug("한글 버그 설명: 로그인 실패")
        filepath = Path(result["file"])
        content = filepath.read_text()
        assert "한글 버그 설명" in content

    def test_empty_evidence_list(self, mk_with_session):
        mk, bug_id = mk_with_session
        evidence = mk.get_evidence(bug_id)
        assert evidence == []

    def test_multiple_evidence_for_same_hypothesis(self, mk_with_hypothesis):
        mk, bug_id = mk_with_hypothesis
        for i in range(5):
            mk.log_evidence(bug_id, "H1", f"Evidence item {i}", result="neutral")
        evidence = mk.get_evidence(bug_id, hypothesis_id="H1")
        assert len(evidence) == 5


# ── 15. Debug Session Constants ───────────────────────────────

class TestDebugConstants:
    def test_debug_states(self):
        mk = MemKraft()
        assert "OBSERVE" in mk.DEBUG_STATES
        assert "HYPOTHESIZE" in mk.DEBUG_STATES
        assert "EXPERIMENT" in mk.DEBUG_STATES
        assert "CONCLUDE" in mk.DEBUG_STATES

    def test_hypothesis_statuses(self):
        mk = MemKraft()
        assert "testing" in mk.HYPOTHESIS_STATUSES
        assert "rejected" in mk.HYPOTHESIS_STATUSES
        assert "confirmed" in mk.HYPOTHESIS_STATUSES

    def test_evidence_results(self):
        mk = MemKraft()
        assert "supports" in mk.EVIDENCE_RESULTS
        assert "contradicts" in mk.EVIDENCE_RESULTS
        assert "neutral" in mk.EVIDENCE_RESULTS


# ── 16. CLI Debug Subcommands ─────────────────────────────────

class TestCLIDebug:
    def test_cli_debug_start(self, tmp_path):
        env = os.environ.copy()
        env["MEMKRAFT_DIR"] = str(tmp_path / "memory")
        result = subprocess.run(
            [sys.executable, "-m", "memkraft.cli", "init"],
            capture_output=True, text=True, env=env
        )
        assert result.returncode == 0
        result = subprocess.run(
            [sys.executable, "-m", "memkraft.cli", "debug", "start", "CLI test bug"],
            capture_output=True, text=True, env=env
        )
        assert result.returncode == 0
        assert "Debug session started" in result.stdout or "DEBUG-" in result.stdout

    def test_cli_debug_history(self, tmp_path):
        env = os.environ.copy()
        env["MEMKRAFT_DIR"] = str(tmp_path / "memory")
        subprocess.run([sys.executable, "-m", "memkraft.cli", "init"], capture_output=True, env=env)
        result = subprocess.run(
            [sys.executable, "-m", "memkraft.cli", "debug", "history"],
            capture_output=True, text=True, env=env
        )
        assert result.returncode == 0

    def test_cli_debug_no_subcommand_shows_help(self, tmp_path):
        env = os.environ.copy()
        env["MEMKRAFT_DIR"] = str(tmp_path / "memory")
        subprocess.run([sys.executable, "-m", "memkraft.cli", "init"], capture_output=True, env=env)
        result = subprocess.run(
            [sys.executable, "-m", "memkraft.cli", "debug"],
            capture_output=True, text=True, env=env
        )
        assert result.returncode == 0


# ── 17. Version Check ────────────────────────────────────────

class TestVersion:
    def test_version_is_042(self):
        from memkraft import __version__
        assert __version__ == "0.8.3"

    def test_memkraft_importable(self):
        from memkraft import MemKraft
        assert MemKraft is not None
