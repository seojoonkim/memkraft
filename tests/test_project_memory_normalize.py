import json
import random

import pytest


def _compile(files, as_of="2026-08-15T00:00:00Z", limits=None):
    from memkraft.project_memory.normalize import normalize_documents
    from memkraft.project_memory.reducer import reduce_observations
    return reduce_observations(normalize_documents(files, limits=limits), as_of=as_of, config={})


def test_markdown_observations_have_heading_locator_and_verbatim_text():
    from memkraft.project_memory.normalize import normalize_documents
    rows = normalize_documents([("docs/x.md", "# Architecture\nintro\n## Storage\nraw  text\n")])
    assert rows[1]["heading_path"] == ["Architecture", "Storage"]
    assert rows[1]["locator"] == {"path": "docs/x.md", "lines": [3, 4]}
    assert rows[1]["excerpt"] == "## Storage\nraw  text\n"
    assert rows[1]["observation_id"].startswith("sha256:")


def test_content_before_first_heading_is_preserved():
    from memkraft.project_memory.normalize import normalize_documents
    rows = normalize_documents([("README.md", "---\ntitle: Demo\n---\nintro prose\n# Heading\nbody\n")])
    assert rows[0]["heading_path"] == []
    assert rows[0]["locator"] == {"path": "README.md", "lines": [1, 4]}
    assert rows[0]["excerpt"] == "---\ntitle: Demo\n---\nintro prose\n"
    assert rows[1]["locator"] == {"path": "README.md", "lines": [5, 6]}


def test_repeated_identical_headings_have_unique_section_ids():
    compiled = _compile([("a.md", "# A\n# A\n")])
    ids = [row["section_id"] for row in compiled["sections"]]
    assert len(ids) == len(set(ids)) == 2


def test_input_permutation_is_byte_identical():
    files = [("b.md", "# B\nbeta\n"), ("a.md", "# A\nalpha\n")]
    a = _compile(files)
    random.shuffle(files)
    b = _compile(files)
    assert a["sections_bytes"] == b["sections_bytes"]
    assert a["semantic_digest"] == b["semantic_digest"]


def test_as_of_only_changes_snapshot_identity():
    a = _compile([("a.md", "# A\nx\n")], "2026-08-15T00:00:00Z")
    b = _compile([("a.md", "# A\nx\n")], "2026-08-16T00:00:00Z")
    assert a["semantic_digest"] == b["semantic_digest"]
    assert a["snapshot_id"] != b["snapshot_id"]


def test_all_limits_fail_closed():
    from memkraft.project_memory.errors import ProjectMemoryError
    cases = [
        ({"max_files": 0}, [("a.md", "x")]),
        ({"max_input_bytes": 1}, [("a.md", "xx")]),
        ({"max_file_bytes": 1}, [("a.md", "xx")]),
        ({"max_line_bytes": 1}, [("a.md", "xx")]),
    ]
    for limits, files in cases:
        with pytest.raises(ProjectMemoryError) as exc:
            _compile(files, limits=limits)
        assert exc.value.code == "E_PM_LIMIT_EXCEEDED"


def test_reduce_from_evidence_is_byte_identical():
    from memkraft.project_memory.normalize import normalize_documents
    from memkraft.project_memory.reducer import reduce_observations, evidence_bytes, observations_from_evidence
    obs = normalize_documents([("a.md", "# A\nbody\n")])
    first = reduce_observations(obs, as_of="2026-08-15T00:00:00Z", config={})
    rebuilt = reduce_observations(observations_from_evidence(evidence_bytes(obs)), as_of="2026-08-15T00:00:00Z", config={})
    assert first["sections_bytes"] == rebuilt["sections_bytes"]
