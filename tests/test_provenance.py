from pathlib import Path
import json

from memkraft import MemKraft


def test_provenance_record_appends_jsonl_and_normalizes_sources(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("alpha beta gamma delta", encoding="utf-8")
    mk = MemKraft(base_dir=str(tmp_path))

    rec = mk.provenance_record(
        "answer-1",
        derived_from=[{"file": "notes.txt", "span": [6, 10]}],
        transform="summarize",
        kind="answer",
        value="beta",
        confidence="0.8",
    )

    assert rec == {
        "record_id": "answer-1",
        "derived_from": [{"file": "notes.txt", "span": [6, 10]}],
        "transform": "summarize",
        "kind": "answer",
        "value": "beta",
        "confidence": "0.8",
    }
    log_path = tmp_path / ".memkraft" / "provenance.jsonl"
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == rec


def test_provenance_record_accepts_positional_string_source_and_path_alias(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("whole source text", encoding="utf-8")
    mk = MemKraft(base_dir=str(tmp_path))

    rec = mk.provenance_record("answer-positional", "notes.txt", value="derived")
    alias_rec = mk.provenance_record("answer-path-alias", {"path": "notes.txt", "line_range": [1, 1]})

    assert rec["derived_from"] == [{"file": "notes.txt"}]
    assert mk.why("answer-positional")["derived_from"][0]["source_text"] == "whole source text"
    assert alias_rec["derived_from"] == [{"path": "notes.txt", "line_range": [1, 1], "file": "notes.txt"}]
    assert mk.why("answer-path-alias")["derived_from"][0]["source_text"] == "whole source text"


def test_why_returns_latest_record_with_source_text_for_span_and_lines(tmp_path):
    (tmp_path / "docs").mkdir()
    rel = tmp_path / "docs" / "note.md"
    rel.write_text("line one\nline two\nline three\n", encoding="utf-8")
    abs_source = tmp_path / "abs.txt"
    abs_source.write_text("0123456789", encoding="utf-8")
    mk = MemKraft(base_dir=str(tmp_path))

    mk.provenance_record(
        "answer-1",
        derived_from=[{"file": "docs/note.md", "lines": [1, 1]}],
        value="old",
    )
    mk.provenance_record(
        "answer-1",
        derived_from=[
            {"file": "docs/note.md", "lines": [2, 3]},
            {"file": str(abs_source), "span": [2, 5]},
            {"file": "missing.txt", "span": [0, 99]},
        ],
        transform="compose",
        value="new",
    )

    why = mk.why("answer-1")

    assert why["record_id"] == "answer-1"
    assert why["value"] == "new"
    assert why["transform"] == "compose"
    assert why["derived_from"][0]["source_text"] == "line two\nline three"
    assert why["derived_from"][1]["source_text"] == "234"
    assert "source_text" not in why["derived_from"][2]


def test_why_skips_corrupt_jsonl_lines_and_missing_records(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    log_path = tmp_path / ".memkraft" / "provenance.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "not json\n"
        + json.dumps({"record_id": "kept", "derived_from": [], "value": "ok"})
        + "\n{broken\n",
        encoding="utf-8",
    )

    assert mk.why("missing") is None
    assert mk.why("kept")["value"] == "ok"
