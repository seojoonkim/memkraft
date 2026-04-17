"""Tests for MemKraft v0.5.0 — Memory Snapshots & Time Travel."""
import json
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from memkraft import MemKraft
from memkraft import __version__


# ── Version ──────────────────────────────────────────────────────────────────

class TestVersion:
    def test_version_is_050(self):
        assert __version__ == "0.8.1"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mk(tmp_path: Path) -> MemKraft:
    mk = MemKraft(str(tmp_path / "memory"))
    mk.init()
    return mk


def _write_entity(mk: MemKraft, name: str, body: str) -> Path:
    """Write a test entity file directly."""
    mk.entities_dir.mkdir(parents=True, exist_ok=True)
    slug = mk._slugify(name)
    p = mk.entities_dir / f"{slug}.md"
    p.write_text(f"# {name}\n\n{body}", encoding="utf-8")
    return p


# ── _file_hash ────────────────────────────────────────────────────────────────

class TestFileHash:
    def test_returns_12_hex_chars(self, tmp_path):
        mk = _mk(tmp_path)
        f = tmp_path / "test.md"
        f.write_text("hello world", encoding="utf-8")
        h = mk._file_hash(f)
        assert len(h) == 12
        assert re.match(r'^[0-9a-f]{12}$', h)

    def test_same_content_same_hash(self, tmp_path):
        mk = _mk(tmp_path)
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("hello", encoding="utf-8")
        b.write_text("hello", encoding="utf-8")
        assert mk._file_hash(a) == mk._file_hash(b)

    def test_different_content_different_hash(self, tmp_path):
        mk = _mk(tmp_path)
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("hello", encoding="utf-8")
        b.write_text("world", encoding="utf-8")
        assert mk._file_hash(a) != mk._file_hash(b)

    def test_missing_file_returns_error(self, tmp_path):
        mk = _mk(tmp_path)
        h = mk._file_hash(tmp_path / "nonexistent.md")
        assert h == "error"


# ── snapshot() ────────────────────────────────────────────────────────────────

class TestSnapshot:
    def test_creates_snapshot_file(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Alice", "CEO of Acme.")
        result = mk.snapshot()
        assert result["file_count"] >= 1
        snap_path = mk.base_dir / result["path"]
        assert snap_path.exists()

    def test_snapshot_json_structure(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Bob", "Engineer.")
        result = mk.snapshot(label="test-snap")
        snap_path = mk.base_dir / result["path"]
        data = json.loads(snap_path.read_text())
        assert data["snapshot_id"].startswith("SNAP-")
        assert "timestamp" in data
        assert data["label"] == "test-snap"
        assert data["memkraft_version"] == "0.8.1"
        assert "files" in data
        assert data["file_count"] >= 1

    def test_snapshot_returns_metadata(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Charlie", "Designer.")
        result = mk.snapshot(label="my-label")
        assert "snapshot_id" in result
        assert result["snapshot_id"].startswith("SNAP-")
        assert result["label"] == "my-label"
        assert result["file_count"] >= 1
        assert result["total_bytes"] > 0
        assert "path" in result

    def test_snapshot_without_label(self, tmp_path):
        mk = _mk(tmp_path)
        result = mk.snapshot()
        assert result["label"] == ""
        assert result["snapshot_id"].startswith("SNAP-")

    def test_snapshot_file_entries_have_required_fields(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write_entity(mk, "Dave", "Product manager.")
        result = mk.snapshot()
        snap_path = mk.base_dir / result["path"]
        data = json.loads(snap_path.read_text())
        # Find the entity file entry
        entry = None
        for rel, fdata in data["files"].items():
            if "dave" in rel:
                entry = fdata
                break
        assert entry is not None, "Entity file not found in snapshot"
        assert "size" in entry
        assert "hash" in entry
        assert "mtime" in entry
        assert "summary" in entry
        assert "sections" in entry
        assert "fact_count" in entry
        assert "link_count" in entry

    def test_snapshot_include_content(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Eve", "Researcher at Lab X.")
        result = mk.snapshot(include_content=True)
        snap_path = mk.base_dir / result["path"]
        data = json.loads(snap_path.read_text())
        has_content = any("content" in fdata for fdata in data["files"].values())
        assert has_content, "include_content=True should embed file text"

    def test_snapshot_without_content_has_no_content_key(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Frank", "Analyst.")
        result = mk.snapshot(include_content=False)
        snap_path = mk.base_dir / result["path"]
        data = json.loads(snap_path.read_text())
        for fdata in data["files"].values():
            assert "content" not in fdata

    def test_snapshot_saved_under_memkraft_snapshots(self, tmp_path):
        mk = _mk(tmp_path)
        result = mk.snapshot()
        assert ".memkraft/snapshots/" in result["path"]

    def test_multiple_snapshots_produce_distinct_ids(self, tmp_path):
        import time
        mk = _mk(tmp_path)
        r1 = mk.snapshot(label="first")
        time.sleep(1.01)
        r2 = mk.snapshot(label="second")
        assert r1["snapshot_id"] != r2["snapshot_id"]

    def test_snapshot_file_count_matches_md_files(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Grace", "Developer.")
        _write_entity(mk, "Hank", "Designer.")
        result = mk.snapshot()
        snap_path = mk.base_dir / result["path"]
        data = json.loads(snap_path.read_text())
        assert data["file_count"] == len(data["files"])


# ── snapshot_list() ───────────────────────────────────────────────────────────

class TestSnapshotList:
    def test_empty_returns_empty_list(self, tmp_path):
        mk = _mk(tmp_path)
        result = mk.snapshot_list()
        assert result == []

    def test_lists_all_snapshots(self, tmp_path):
        import time
        mk = _mk(tmp_path)
        mk.snapshot(label="alpha")
        time.sleep(1.01)
        mk.snapshot(label="beta")
        snaps = mk.snapshot_list()
        assert len(snaps) == 2

    def test_newest_first(self, tmp_path):
        import time
        mk = _mk(tmp_path)
        mk.snapshot(label="first")
        time.sleep(1.01)
        mk.snapshot(label="second")
        snaps = mk.snapshot_list()
        # newest first → "second" should come before "first"
        labels = [s["label"] for s in snaps]
        assert labels.index("second") < labels.index("first")

    def test_snapshot_list_entry_fields(self, tmp_path):
        mk = _mk(tmp_path)
        mk.snapshot(label="test")
        snaps = mk.snapshot_list()
        assert len(snaps) == 1
        s = snaps[0]
        assert "snapshot_id" in s
        assert "timestamp" in s
        assert "label" in s
        assert "file_count" in s
        assert "total_bytes" in s

    def test_label_preserved(self, tmp_path):
        mk = _mk(tmp_path)
        mk.snapshot(label="my-special-label")
        snaps = mk.snapshot_list()
        assert snaps[0]["label"] == "my-special-label"


# ── _load_snapshot() ──────────────────────────────────────────────────────────

class TestLoadSnapshot:
    def test_load_by_exact_id(self, tmp_path):
        mk = _mk(tmp_path)
        result = mk.snapshot(label="exact")
        data = mk._load_snapshot(result["snapshot_id"])
        assert data is not None
        assert data["snapshot_id"] == result["snapshot_id"]

    def test_load_by_partial_id(self, tmp_path):
        mk = _mk(tmp_path)
        result = mk.snapshot()
        # Use last 8 chars as partial match
        partial = result["snapshot_id"][-8:]
        data = mk._load_snapshot(partial)
        assert data is not None

    def test_load_by_label(self, tmp_path):
        mk = _mk(tmp_path)
        mk.snapshot(label="find-me-by-label")
        data = mk._load_snapshot("find-me-by-label")
        assert data is not None
        assert data["label"] == "find-me-by-label"

    def test_returns_none_for_missing(self, tmp_path):
        mk = _mk(tmp_path)
        result = mk._load_snapshot("SNAP-nonexistent-id")
        assert result is None


# ── snapshot_diff() ───────────────────────────────────────────────────────────

class TestSnapshotDiff:
    def test_diff_with_missing_snapshot_returns_empty(self, tmp_path):
        mk = _mk(tmp_path)
        result = mk.snapshot_diff("SNAP-does-not-exist")
        assert result == {}

    def test_diff_vs_live_unchanged(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Ivan", "Founder.")
        r = mk.snapshot()
        diff = mk.snapshot_diff(r["snapshot_id"])
        # Nothing changed since snapshot
        assert diff["added"] == []
        assert diff["removed"] == []
        assert diff["modified"] == []
        assert diff["unchanged_count"] >= 1

    def test_diff_vs_live_detects_new_file(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Jane", "Engineer.")
        r = mk.snapshot()
        # Add a new entity after snapshot
        _write_entity(mk, "Karl", "Designer.")
        diff = mk.snapshot_diff(r["snapshot_id"])
        added_files = [a["file"] for a in diff["added"]]
        assert any("karl" in f for f in added_files)

    def test_diff_vs_live_detects_modified_file(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write_entity(mk, "Lena", "Original content.")
        r = mk.snapshot()
        # Modify the file
        p.write_text("# Lena\n\nCompletely different content now.", encoding="utf-8")
        diff = mk.snapshot_diff(r["snapshot_id"])
        modified_files = [m["file"] for m in diff["modified"]]
        assert any("lena" in f for f in modified_files)

    def test_diff_vs_live_detects_removed_file(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write_entity(mk, "Mia", "Will be deleted.")
        r = mk.snapshot()
        p.unlink()
        diff = mk.snapshot_diff(r["snapshot_id"])
        removed_files = [rem["file"] for rem in diff["removed"]]
        assert any("mia" in f for f in removed_files)

    def test_diff_between_two_snapshots(self, tmp_path):
        import time
        mk = _mk(tmp_path)
        _write_entity(mk, "Nick", "Version 1.")
        r1 = mk.snapshot(label="before")
        time.sleep(1.01)
        _write_entity(mk, "Olivia", "New person.")
        r2 = mk.snapshot(label="after")
        diff = mk.snapshot_diff(r1["snapshot_id"], r2["snapshot_id"])
        assert diff["snapshot_a"] == r1["snapshot_id"]
        assert diff["snapshot_b"] == r2["snapshot_id"]
        added_files = [a["file"] for a in diff["added"]]
        assert any("olivia" in f for f in added_files)

    def test_diff_result_structure(self, tmp_path):
        mk = _mk(tmp_path)
        r = mk.snapshot()
        diff = mk.snapshot_diff(r["snapshot_id"])
        assert "snapshot_a" in diff
        assert "snapshot_b" in diff
        assert "added" in diff
        assert "removed" in diff
        assert "modified" in diff
        assert "unchanged_count" in diff

    def test_diff_modified_has_delta(self, tmp_path):
        mk = _mk(tmp_path)
        p = _write_entity(mk, "Pat", "Short.")
        r = mk.snapshot()
        p.write_text("# Pat\n\nMuch longer content added here with many more words.", encoding="utf-8")
        diff = mk.snapshot_diff(r["snapshot_id"])
        assert len(diff["modified"]) == 1
        mod = diff["modified"][0]
        assert "delta" in mod
        assert mod["delta"] > 0  # grew


# ── time_travel() ────────────────────────────────────────────────────────────

class TestTimeTravel:
    def test_returns_empty_with_no_snapshots(self, tmp_path):
        mk = _mk(tmp_path)
        result = mk.time_travel("something")
        assert result == []

    def test_finds_entity_in_snapshot(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Quinn", "Blockchain researcher at Protocol Labs.")
        r = mk.snapshot()
        results = mk.time_travel("blockchain", snapshot_id=r["snapshot_id"])
        assert len(results) > 0
        files = [res["file"] for res in results]
        assert any("quinn" in f for f in files)

    def test_returns_sorted_by_score(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Robot", "AI researcher. Loves robots and automation.")
        _write_entity(mk, "Sam", "General person.")
        r = mk.snapshot()
        results = mk.time_travel("AI", snapshot_id=r["snapshot_id"])
        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"]

    def test_result_has_required_fields(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Tina", "CEO of TechCorp.")
        r = mk.snapshot()
        results = mk.time_travel("CEO", snapshot_id=r["snapshot_id"])
        assert len(results) > 0
        res = results[0]
        assert "file" in res
        assert "score" in res
        assert "match" in res
        assert "snapshot" in res
        assert res["snapshot"] == r["snapshot_id"]

    def test_time_travel_with_content_gives_higher_score(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Uma", "Deep learning specialist. Works on transformers.")
        r_no_content = mk.snapshot(include_content=False)
        import time; time.sleep(1.01)
        r_with_content = mk.snapshot(include_content=True)
        res_no = mk.time_travel("transformers", snapshot_id=r_no_content["snapshot_id"])
        res_yes = mk.time_travel("transformers", snapshot_id=r_with_content["snapshot_id"])
        # With content should find at least as many results
        assert len(res_yes) >= len(res_no)

    def test_time_travel_no_results_for_unknown_query(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Vera", "Accountant.")
        r = mk.snapshot()
        results = mk.time_travel("xyzabcnotfound999", snapshot_id=r["snapshot_id"])
        assert results == []

    def test_time_travel_by_date_finds_closest_snapshot(self, tmp_path):
        import time
        mk = _mk(tmp_path)
        _write_entity(mk, "Walt", "Historian expert.")
        mk.snapshot(label="snap1", include_content=True)
        time.sleep(1.01)
        _write_entity(mk, "Xena", "Warrior princess.")
        mk.snapshot(label="snap2", include_content=True)
        # Travel to today → should use latest snapshot
        today = __import__('datetime').date.today().isoformat()
        results = mk.time_travel("warrior", date=today)
        # Should find Xena (present in snap2, which is closest to today)
        assert any("xena" in r["file"] for r in results)

    def test_time_travel_invalid_date_returns_empty(self, tmp_path):
        mk = _mk(tmp_path)
        mk.snapshot()
        result = mk.time_travel("anything", date="not-a-date")
        assert result == []

    def test_time_travel_uses_latest_snapshot_when_no_args(self, tmp_path):
        import time
        mk = _mk(tmp_path)
        _write_entity(mk, "Yuki", "Developer.")
        mk.snapshot(label="old", include_content=True)
        time.sleep(1.01)
        _write_entity(mk, "Zara", "Designer professional.")
        mk.snapshot(label="new", include_content=True)
        # No snapshot_id or date → uses latest
        results = mk.time_travel("designer")
        assert any("zara" in r["file"] for r in results)


# ── snapshot_entity() ────────────────────────────────────────────────────────

class TestSnapshotEntity:
    def test_returns_empty_with_no_snapshots(self, tmp_path):
        mk = _mk(tmp_path)
        result = mk.snapshot_entity("NoOne")
        assert result == []

    def test_tracks_entity_across_snapshots(self, tmp_path):
        import time
        mk = _mk(tmp_path)
        p = _write_entity(mk, "Apollo", "Version 1 of Apollo.")
        mk.snapshot(label="v1")
        time.sleep(1.01)
        p.write_text("# Apollo\n\nVersion 2: More details added.", encoding="utf-8")
        mk.snapshot(label="v2")
        timeline = mk.snapshot_entity("Apollo")
        assert len(timeline) >= 2
        change_types = [t["change_type"] for t in timeline]
        assert "new" in change_types
        assert "modified" in change_types

    def test_detects_entity_deletion(self, tmp_path):
        import time
        mk = _mk(tmp_path)
        p = _write_entity(mk, "Brutus", "Will be deleted.")
        mk.snapshot(label="before-delete")
        time.sleep(1.01)
        p.unlink()
        mk.snapshot(label="after-delete")
        timeline = mk.snapshot_entity("Brutus")
        change_types = [t["change_type"] for t in timeline]
        assert "deleted" in change_types

    def test_unchanged_entity_shows_unchanged(self, tmp_path):
        import time
        mk = _mk(tmp_path)
        _write_entity(mk, "Caesar", "Stable entity.")
        mk.snapshot(label="snap-a")
        time.sleep(1.01)
        mk.snapshot(label="snap-b")
        timeline = mk.snapshot_entity("Caesar")
        change_types = [t["change_type"] for t in timeline]
        assert "unchanged" in change_types

    def test_entity_timeline_fields(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Diana", "Researcher.")
        mk.snapshot()
        timeline = mk.snapshot_entity("Diana")
        assert len(timeline) >= 1
        t = timeline[0]
        assert "snapshot_id" in t
        assert "timestamp" in t
        assert "file" in t
        assert "fact_count" in t
        assert "size" in t
        assert "hash" in t
        assert "change_type" in t

    def test_entity_not_in_snapshots_returns_empty_timeline(self, tmp_path):
        mk = _mk(tmp_path)
        _write_entity(mk, "Echo", "Exists now.")
        mk.snapshot()
        # Query for an entity that was never in any snapshot
        timeline = mk.snapshot_entity("Phantom-Entity-Never-Existed-99")
        assert timeline == []


# ── Integration: full snapshot workflow ──────────────────────────────────────

class TestSnapshotWorkflow:
    def test_snapshot_diff_time_travel_roundtrip(self, tmp_path):
        """Full workflow: snapshot → modify → diff → time-travel."""
        import time
        mk = _mk(tmp_path)

        # Setup initial state
        _write_entity(mk, "Firm", "Initial description of Firm.")
        snap_before = mk.snapshot(label="before")

        time.sleep(1.01)

        # Modify
        firm_path = mk.entities_dir / "firm.md"
        firm_path.write_text("# Firm\n\nRevised description. New CEO hired.", encoding="utf-8")
        _write_entity(mk, "NewPerson", "Just joined.")
        snap_after = mk.snapshot(label="after")

        # Diff should show changes
        diff = mk.snapshot_diff(snap_before["snapshot_id"], snap_after["snapshot_id"])
        assert len(diff["modified"]) >= 1 or len(diff["added"]) >= 1

        # Time-travel back to before should find original content via summary
        results_before = mk.time_travel("Initial", snapshot_id=snap_before["snapshot_id"])
        assert any("firm" in r["file"] for r in results_before)

    def test_init_creates_snapshots_dir(self, tmp_path):
        mk = _mk(tmp_path)
        assert (mk.base_dir / ".memkraft" / "snapshots").exists()

    def test_snapshot_count_grows(self, tmp_path):
        import time
        mk = _mk(tmp_path)
        mk.snapshot()
        time.sleep(1.01)
        mk.snapshot()
        time.sleep(1.01)
        mk.snapshot()
        snaps = mk.snapshot_list()
        assert len(snaps) == 3

    def test_snapshot_ids_are_timestamp_based(self, tmp_path):
        mk = _mk(tmp_path)
        result = mk.snapshot()
        # SNAP-YYYYMMDD-HHMMSS format
        assert re.match(r'^SNAP-\d{8}-\d{6}(-[a-f0-9]+)?$', result["snapshot_id"])
