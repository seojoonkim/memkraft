# tests/test_v040_debug_hypothesis.py
"""Test MemKraft v0.4.0 Debug Hypothesis Tracking features"""

import pytest
import tempfile
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from memkraft.core import MemKraft
from memkraft import __version__


class TestDebugHypothesis:
    @pytest.fixture
    def mk(self, tmp_path):
        mk = MemKraft(str(tmp_path))
        mk.init()
        return mk

    # --- Version ---

    def test_version_040(self):
        """Version should be 0.4.0."""
        assert __version__ == "0.4.0"

    # --- start_debug ---

    def test_start_debug_creates_file(self, mk, tmp_path):
        result = mk.start_debug("Test bug: TypeError in parser")
        assert result["bug_id"].startswith("DEBUG-")
        assert result["status"] == "OBSERVE"
        debug_file = tmp_path / "debug" / f"{result['bug_id']}.md"
        assert debug_file.exists()
        content = debug_file.read_text()
        assert "Test bug: TypeError in parser" in content
        assert "**Status:** OBSERVE" in content

    def test_start_debug_returns_dict(self, mk):
        result = mk.start_debug("Simple bug")
        assert isinstance(result, dict)
        assert "bug_id" in result
        assert "file" in result
        assert "status" in result

    def test_start_debug_unique_ids(self, mk):
        """Multiple sessions in same second should get unique IDs."""
        debug1 = mk.start_debug("Bug 1")
        debug2 = mk.start_debug("Bug 2")
        debug3 = mk.start_debug("Bug 3")
        ids = {debug1["bug_id"], debug2["bug_id"], debug3["bug_id"]}
        assert len(ids) == 3, "All bug_ids must be unique"

    def test_start_debug_file_content_structure(self, mk, tmp_path):
        result = mk.start_debug("Check structure")
        filepath = tmp_path / "debug" / f"{result['bug_id']}.md"
        content = filepath.read_text()
        assert "## Observation" in content
        assert "## Hypotheses" in content
        assert "## Evidence Log" in content
        assert "## Conclusion" in content
        assert "## Timeline" in content

    # --- log_hypothesis ---

    def test_log_hypothesis_valid_status(self, mk):
        debug = mk.start_debug("Test bug")
        result = mk.log_hypothesis(debug["bug_id"], "Hypothesis: wrong regex pattern")
        assert result["status"] == "testing"
        assert "H1" in result["hypothesis_id"]

    def test_log_hypothesis_invalid_status(self, mk):
        debug = mk.start_debug("Test bug")
        result = mk.log_hypothesis(debug["bug_id"], "Test hyp", status="invalid")
        assert result == {}  # Empty dict on error

    def test_log_hypothesis_creates_entry(self, mk):
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "Hypothesis 1: missing import")
        hypotheses = mk.get_hypotheses(debug["bug_id"])
        assert len(hypotheses) == 1
        assert hypotheses[0]["hypothesis_id"] == "H1"
        assert "missing import" in hypotheses[0]["hypothesis"]

    def test_log_multiple_hypotheses(self, mk):
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "H1: import issue")
        mk.log_hypothesis(debug["bug_id"], "H2: regex issue")
        hypotheses = mk.get_hypotheses(debug["bug_id"])
        assert len(hypotheses) == 2
        assert hypotheses[1]["hypothesis_id"] == "H2"

    def test_hypothesis_with_evidence(self, mk):
        debug = mk.start_debug("With evidence")
        mk.log_hypothesis(debug["bug_id"], "H1 with evidence", evidence="stacktrace shows...")
        content = mk._get_debug_file(debug["bug_id"]).read_text()
        assert "Initial evidence: stacktrace shows" in content

    def test_log_hypothesis_nonexistent_session(self, mk):
        result = mk.log_hypothesis("DEBUG-FAKE-000000", "test")
        assert result == {}

    def test_log_hypothesis_all_valid_statuses(self, mk):
        """Test all three valid statuses for hypotheses."""
        debug = mk.start_debug("Status test")
        r1 = mk.log_hypothesis(debug["bug_id"], "H1", status="testing")
        assert r1["status"] == "testing"
        r2 = mk.log_hypothesis(debug["bug_id"], "H2", status="rejected")
        assert r2["status"] == "rejected"
        r3 = mk.log_hypothesis(debug["bug_id"], "H3", status="confirmed")
        assert r3["status"] == "confirmed"

    # --- get_hypotheses ---

    def test_get_hypotheses_empty(self, mk):
        debug = mk.start_debug("Test bug")
        hypotheses = mk.get_hypotheses(debug["bug_id"])
        assert hypotheses == []

    def test_get_hypotheses_nonexistent_session(self, mk):
        result = mk.get_hypotheses("DEBUG-FAKE-000000")
        assert result == []

    def test_get_hypotheses_after_reject(self, mk):
        """Hypotheses should still be retrievable after rejection."""
        debug = mk.start_debug("Reject test")
        mk.log_hypothesis(debug["bug_id"], "Test hyp")
        mk.reject_hypothesis(debug["bug_id"], "H1", "didn't work")
        hypotheses = mk.get_hypotheses(debug["bug_id"])
        assert len(hypotheses) == 1
        assert hypotheses[0]["status"] == "rejected"

    def test_get_hypotheses_after_confirm(self, mk):
        """Hypotheses should still be retrievable after confirmation."""
        debug = mk.start_debug("Confirm test")
        mk.log_hypothesis(debug["bug_id"], "Working hyp")
        mk.confirm_hypothesis(debug["bug_id"], "H1")
        hypotheses = mk.get_hypotheses(debug["bug_id"])
        assert len(hypotheses) == 1
        assert hypotheses[0]["status"] == "confirmed"

    # --- reject_hypothesis ---

    def test_reject_hypothesis_updates_status(self, mk):
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "Test hyp")
        result = mk.reject_hypothesis(debug["bug_id"], "H1", "reason: didnt work")
        assert result["status"] == "rejected"
        assert result["reason"] == "reason: didnt work"

        hypotheses = mk.get_hypotheses(debug["bug_id"])
        assert hypotheses[0]["status"] == "rejected"

    def test_reject_nonexistent_hypothesis(self, mk):
        debug = mk.start_debug("Test bug")
        result = mk.reject_hypothesis(debug["bug_id"], "H999")
        assert result == {}

    def test_reject_already_rejected(self, mk):
        """Rejecting an already rejected hypothesis returns empty."""
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "H1")
        mk.reject_hypothesis(debug["bug_id"], "H1", "first reason")
        result = mk.reject_hypothesis(debug["bug_id"], "H1", "second reason")
        assert result == {}

    # --- confirm_hypothesis ---

    def test_confirm_hypothesis_updates_status(self, mk):
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "Working hyp")
        mk.confirm_hypothesis(debug["bug_id"], "H1")

        hypotheses = mk.get_hypotheses(debug["bug_id"])
        assert hypotheses[0]["status"] == "confirmed"

    def test_confirm_non_testing_hypothesis(self, mk):
        debug = mk.start_debug("Confirm error test")
        mk.log_hypothesis(debug["bug_id"], "H1")
        mk.reject_hypothesis(debug["bug_id"], "H1")
        result = mk.confirm_hypothesis(debug["bug_id"], "H1")
        assert result == {}  # Should fail — already rejected

    def test_confirm_nonexistent_hypothesis(self, mk):
        debug = mk.start_debug("Test")
        result = mk.confirm_hypothesis(debug["bug_id"], "H999")
        assert result == {}

    # --- log_evidence ---

    def test_log_evidence(self, mk):
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "Test hyp")
        result = mk.log_evidence(debug["bug_id"], "H1", "Stack trace shows line 42", "supports")
        assert result["result"] == "supports"

    def test_evidence_invalid_result(self, mk):
        debug = mk.start_debug("Invalid result test")
        mk.log_hypothesis(debug["bug_id"], "H1")
        result = mk.log_evidence(debug["bug_id"], "H1", "test", "invalid")
        assert result == {}

    def test_log_evidence_all_types(self, mk):
        """Test all three evidence result types."""
        debug = mk.start_debug("Evidence types")
        mk.log_hypothesis(debug["bug_id"], "H1")
        r1 = mk.log_evidence(debug["bug_id"], "H1", "supports this", "supports")
        r2 = mk.log_evidence(debug["bug_id"], "H1", "contradicts this", "contradicts")
        r3 = mk.log_evidence(debug["bug_id"], "H1", "neutral to this", "neutral")
        assert r1["result"] == "supports"
        assert r2["result"] == "contradicts"
        assert r3["result"] == "neutral"

    # --- get_evidence ---

    def test_get_evidence(self, mk):
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "Test hyp")
        mk.log_evidence(debug["bug_id"], "H1", "Evidence 1", "supports")
        mk.log_evidence(debug["bug_id"], "H1", "Evidence 2", "contradicts")

        evidence = mk.get_evidence(debug["bug_id"], "H1")
        assert len(evidence) == 2
        assert evidence[0]["result"] == "supports"
        assert evidence[1]["result"] == "contradicts"

    def test_get_evidence_all(self, mk):
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "H1")
        mk.log_hypothesis(debug["bug_id"], "H2")
        mk.log_evidence(debug["bug_id"], "H1", "E1", "supports")
        mk.log_evidence(debug["bug_id"], "H2", "E2", "neutral")

        evidence = mk.get_evidence(debug["bug_id"])  # All evidence
        assert len(evidence) == 2

    def test_get_evidence_filtered(self, mk):
        debug = mk.start_debug("Filter test")
        mk.log_hypothesis(debug["bug_id"], "H1")
        mk.log_hypothesis(debug["bug_id"], "H2")
        mk.log_evidence(debug["bug_id"], "H1", "H1 evidence", "supports")
        mk.log_evidence(debug["bug_id"], "H2", "H2 evidence", "neutral")

        h1_evidence = mk.get_evidence(debug["bug_id"], "H1")
        assert len(h1_evidence) == 1
        assert h1_evidence[0]["hypothesis_id"] == "H1"

    def test_get_evidence_empty(self, mk):
        debug = mk.start_debug("No evidence")
        mk.log_hypothesis(debug["bug_id"], "H1")
        evidence = mk.get_evidence(debug["bug_id"], "H1")
        assert evidence == []

    # --- end_debug ---

    def test_end_debug(self, mk):
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "Test hyp")
        mk.log_evidence(debug["bug_id"], "H1", "Evidence", "supports")
        result = mk.end_debug(debug["bug_id"], "Fixed by adding missing import")
        assert result["status"] == "CONCLUDE"

    def test_end_debug_feeds_back(self, mk):
        debug = mk.start_debug("Feedback test")
        mk.log_hypothesis(debug["bug_id"], "Confirmed: missing null check")
        mk.confirm_hypothesis(debug["bug_id"], "H1")
        mk.end_debug(debug["bug_id"], "Added null check at line 42")

        # Should have confirmed hypothesis count
        result = mk.end_debug(debug["bug_id"], "test")  # Already ended
        assert result["hypotheses_confirmed"] == 1

    def test_end_debug_nonexistent(self, mk):
        result = mk.end_debug("DEBUG-FAKE-000000", "test")
        assert result == {}

    # --- get_debug_status ---

    def test_get_debug_status(self, mk):
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "H1: test")
        mk.log_evidence(debug["bug_id"], "H1", "test evidence", "supports")

        status = mk.get_debug_status(debug["bug_id"])
        assert status["status"] == "EXPERIMENT"
        assert status["hypotheses_total"] == 1
        assert status["evidence_count"] == 1
        assert status["current_hypothesis"] == "H1"

    def test_debug_status_no_hypotheses(self, mk):
        debug = mk.start_debug("No hyp test")
        status = mk.get_debug_status(debug["bug_id"])
        assert status["hypotheses_total"] == 0
        assert status["current_hypothesis"] is None

    def test_debug_status_concluded(self, mk):
        debug = mk.start_debug("Concluded test")
        mk.end_debug(debug["bug_id"], "fixed")
        status = mk.get_debug_status(debug["bug_id"])
        assert status["status"] == "CONCLUDE"

    def test_status_shows_rejected_count(self, mk):
        debug = mk.start_debug("Status rejected test")
        mk.log_hypothesis(debug["bug_id"], "H1")
        mk.reject_hypothesis(debug["bug_id"], "H1")
        mk.log_hypothesis(debug["bug_id"], "H2")
        mk.reject_hypothesis(debug["bug_id"], "H2")

        status = mk.get_debug_status(debug["bug_id"])
        assert status["hypotheses_rejected"] == 2

    def test_status_shows_description(self, mk):
        debug = mk.start_debug("Unique bug description XYZ123")
        status = mk.get_debug_status(debug["bug_id"])
        assert "XYZ123" in status["description"]

    def test_status_nonexistent(self, mk):
        result = mk.get_debug_status("DEBUG-FAKE-000000")
        assert result == {}

    # --- debug_history ---

    def test_debug_history(self, mk):
        mk.start_debug("Bug 1")
        mk.start_debug("Bug 2")
        mk.start_debug("Bug 3")

        history = mk.debug_history(limit=5)
        assert len(history) == 3

    def test_debug_history_limit(self, mk):
        for i in range(12):
            mk.start_debug(f"History test {i}")
        history = mk.debug_history(limit=5)
        assert len(history) == 5

    def test_debug_history_empty(self, mk):
        history = mk.debug_history()
        assert history == []

    # --- search_rejected_hypotheses ---

    def test_search_rejected_hypotheses(self, mk):
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "Rejected: wrong regex")
        mk.reject_hypothesis(debug["bug_id"], "H1", "didnt match edge case")

        results = mk.search_rejected_hypotheses("regex")
        assert len(results) == 1
        assert results[0]["status"] == "rejected"

    def test_search_rejected_no_match(self, mk):
        mk.start_debug("No match test")
        results = mk.search_rejected_hypotheses("nonexistent-hypothesis")
        assert len(results) == 0

    def test_search_rejected_by_reason(self, mk):
        """Should find rejected hypotheses by searching the reason text."""
        debug = mk.start_debug("Test bug")
        mk.log_hypothesis(debug["bug_id"], "H1: some hypothesis")
        mk.reject_hypothesis(debug["bug_id"], "H1", "database connection timeout")

        results = mk.search_rejected_hypotheses("timeout")
        assert len(results) >= 1

    # --- search_debug_sessions ---

    def test_search_debug_sessions(self, mk):
        mk.start_debug("Search test: parser error")
        mk.start_debug("Search test: type error")

        results = mk.search_debug_sessions("parser")
        assert len(results) >= 1

    def test_search_debug_sessions_no_match(self, mk):
        mk.start_debug("Some bug")
        results = mk.search_debug_sessions("completely-unrelated-xyz")
        assert len(results) == 0

    # --- auto_switch_warning ---

    def test_auto_switch_warning(self, mk):
        debug = mk.start_debug("Test 2-fail switch")
        mk.log_hypothesis(debug["bug_id"], "H1")
        mk.reject_hypothesis(debug["bug_id"], "H1")
        mk.log_hypothesis(debug["bug_id"], "H2")
        mk.reject_hypothesis(debug["bug_id"], "H2")

        # 3rd hypothesis should show warning
        mk.log_hypothesis(debug["bug_id"], "H3: different approach")

        content = mk._get_debug_file(debug["bug_id"]).read_text()
        assert "2 hypotheses rejected" in content

    # --- CLI integration ---

    def test_cli_integration_start(self, mk, tmp_path):
        debug = mk.start_debug("CLI test bug")
        assert Path(tmp_path / "debug" / f"{debug['bug_id']}.md").exists()

    # --- Full workflow tests ---

    def test_full_debug_workflow(self, mk):
        """Test complete OBSERVE→HYPOTHESIZE→EXPERIMENT→CONCLUDE flow."""

        # OBSERVE
        debug = mk.start_debug("Integration test: API returns 500 on POST /users")

        # HYPOTHESIZE → REJECT
        mk.log_hypothesis(debug["bug_id"], "H1: Database connection timeout", evidence="Logs show DB timeout")
        mk.log_evidence(debug["bug_id"], "H1", "Increased connection pool size", "neutral")
        mk.reject_hypothesis(debug["bug_id"], "H1", "Still timing out after pool increase")

        # HYPOTHESIZE → REJECT (triggers 2-fail warning)
        mk.log_hypothesis(debug["bug_id"], "H2: Missing input validation", evidence="No validation middleware")
        mk.log_evidence(debug["bug_id"], "H2", "Added validation, still 500", "contradicts")
        mk.reject_hypothesis(debug["bug_id"], "H2", "Validation passed but server crashed")

        # HYPOTHESIZE → CONFIRM
        mk.log_hypothesis(debug["bug_id"], "H3: Null pointer in user serializer")
        mk.log_evidence(debug["bug_id"], "H3", "Found null email in test data", "supports")
        mk.log_evidence(debug["bug_id"], "H3", "Added null check, API works", "supports")
        mk.confirm_hypothesis(debug["bug_id"], "H3")

        # CONCLUDE
        mk.end_debug(debug["bug_id"], "Fixed null pointer exception in UserSerializer by adding email null check (line 127)")

        # Verify final state
        status = mk.get_debug_status(debug["bug_id"])
        assert status["status"] == "CONCLUDE"
        assert status["hypotheses_confirmed"] == 1
        assert status["hypotheses_rejected"] == 2

        hypotheses = mk.get_hypotheses(debug["bug_id"])
        assert len(hypotheses) == 3
        statuses = [h["status"] for h in hypotheses]
        assert statuses == ["rejected", "rejected", "confirmed"]

        # Verify search finds rejected hypotheses
        rejected = mk.search_rejected_hypotheses("validation")
        assert len(rejected) == 1

        # Verify search finds sessions
        sessions = mk.search_debug_sessions("serializer")
        assert len(sessions) >= 1

    def test_regression_existing_features(self, mk):
        """Ensure debug features don't break existing functionality."""

        # Test existing extract still works
        mk.extract("CEO John Doe founded Acme Corp in 2020.", source="test")

        # Test dream still works
        dream_result = mk.dream(dry_run=True)
        assert isinstance(dream_result, dict)
        assert "issues" in dream_result

        # Test search still works
        mk.track("TestEntity", source="regression")
        results = mk.search("TestEntity")
        assert len(results) > 0

    # --- Edge cases ---

    def test_debug_dir_created_on_init(self, mk, tmp_path):
        """debug/ directory should exist after init."""
        assert (tmp_path / "debug").is_dir()

    def test_evidence_logged_in_correct_section(self, mk):
        """Evidence should appear between Evidence Log and Conclusion sections."""
        debug = mk.start_debug("Section test")
        mk.log_hypothesis(debug["bug_id"], "H1")
        mk.log_evidence(debug["bug_id"], "H1", "Test evidence text", "supports")

        content = mk._get_debug_file(debug["bug_id"]).read_text()
        ev_idx = content.index("## Evidence Log")
        conc_idx = content.index("## Conclusion")
        text_idx = content.index("Test evidence text")
        assert ev_idx < text_idx < conc_idx

    def test_timeline_entries_added(self, mk):
        """Timeline should have entries for each action."""
        debug = mk.start_debug("Timeline test")
        mk.log_hypothesis(debug["bug_id"], "Test hyp")
        mk.reject_hypothesis(debug["bug_id"], "H1", "bad")
        mk.end_debug(debug["bug_id"], "done")

        content = mk._get_debug_file(debug["bug_id"]).read_text()
        timeline_section = content.split("## Timeline")[1]
        assert "Debug session started" in timeline_section
        assert "Hypothesis H1 added" in timeline_section
        assert "H1 rejected" in timeline_section
        assert "concluded" in timeline_section

    def test_multiple_sessions_independent(self, mk):
        """Actions on one session should not affect another."""
        d1 = mk.start_debug("Session A")
        d2 = mk.start_debug("Session B")

        mk.log_hypothesis(d1["bug_id"], "H1 for A")
        mk.log_hypothesis(d2["bug_id"], "H1 for B")

        h1 = mk.get_hypotheses(d1["bug_id"])
        h2 = mk.get_hypotheses(d2["bug_id"])
        assert len(h1) == 1
        assert len(h2) == 1
        assert "for A" in h1[0]["hypothesis"]
        assert "for B" in h2[0]["hypothesis"]

    def test_hypothesis_statuses_constant(self):
        """HYPOTHESIS_STATUSES should contain the three valid statuses."""
        assert "testing" in MemKraft.HYPOTHESIS_STATUSES
        assert "rejected" in MemKraft.HYPOTHESIS_STATUSES
        assert "confirmed" in MemKraft.HYPOTHESIS_STATUSES

    def test_evidence_results_constant(self):
        """EVIDENCE_RESULTS should contain the three valid results."""
        assert "supports" in MemKraft.EVIDENCE_RESULTS
        assert "contradicts" in MemKraft.EVIDENCE_RESULTS
        assert "neutral" in MemKraft.EVIDENCE_RESULTS

    def test_start_debug_bug_id_format(self, mk):
        """Bug ID should follow DEBUG-YYYYMMDD-HHMMSS format."""
        import re
        result = mk.start_debug("Format test")
        assert re.match(r"DEBUG-\d{8}-\d{6}(-\d+)?$", result["bug_id"])

    def test_get_debug_file_returns_path(self, mk):
        debug = mk.start_debug("Path test")
        filepath = mk._get_debug_file(debug["bug_id"])
        assert filepath is not None
        assert filepath.exists()
        assert filepath.suffix == ".md"

    def test_get_debug_file_nonexistent(self, mk):
        result = mk._get_debug_file("DEBUG-NONEXISTENT")
        assert result is None

    def test_hypothesis_count_increments(self, mk):
        """Hypothesis IDs should increment: H1, H2, H3..."""
        debug = mk.start_debug("Increment test")
        for i in range(5):
            result = mk.log_hypothesis(debug["bug_id"], f"Hypothesis {i+1}")
            assert result["hypothesis_id"] == f"H{i+1}"

    def test_end_debug_resolution_in_file(self, mk):
        """Resolution text should appear in the debug file."""
        debug = mk.start_debug("Resolution test")
        resolution_text = "Fixed by refactoring the parser module"
        mk.end_debug(debug["bug_id"], resolution_text)
        content = mk._get_debug_file(debug["bug_id"]).read_text()
        assert resolution_text in content
