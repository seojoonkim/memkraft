from __future__ import annotations

import json
from pathlib import Path

import pytest

from memkraft import MemKraft


def _write(base: Path, relative: str, text: str) -> None:
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_compile_evidence_context_preserves_multiple_verbatim_spans_and_hit_order(tmp_path):
    _write(
        tmp_path,
        "inbox/session.md",
        "intro\nBought Midnight Sky EP at the festival.\n"
        "filler one\nfiller two\nfiller three\n"
        "Later I downloaded Happier Than Ever.\noutro\n",
    )
    _write(tmp_path, "inbox/other.md", "I bought a different album.\n")
    mk = MemKraft(str(tmp_path))
    supplied = [
        {"id": "first", "file": "inbox/session.md", "score": 0.9},
        {"id": "second", "file": "inbox/other.md", "score": 0.8},
    ]

    result = mk.compile_evidence_context(
        "bought album downloaded",
        results=supplied,
        budget=400,
        window_chars=100,
        pinned_sources=["inbox/other.md"],
    )

    assert supplied == [
        {"id": "first", "file": "inbox/session.md", "score": 0.9},
        {"id": "second", "file": "inbox/other.md", "score": 0.8},
    ]
    assert [item["file"] for item in result["evidence"][:2]] == [
        "inbox/session.md",
        "inbox/other.md",
    ]
    assert result["evidence"][0]["hit"]["id"] == "first"
    session_items = [item for item in result["evidence"] if item["file"] == "inbox/session.md"]
    assert len(session_items) >= 2
    source = (tmp_path / "inbox/session.md").read_text(encoding="utf-8")
    for item in result["evidence"]:
        content = (tmp_path / item["file"]).read_text(encoding="utf-8")
        start, end = item["span"]
        assert item["text"] == content[start:end]
    assert "Bought Midnight Sky" in result["text"]
    assert "downloaded Happier" in result["text"]
    assert result["estimated_tokens"] <= result["budget"]
    assert result == mk.compile_evidence_context(
        "bought album downloaded",
        results=supplied,
        budget=400,
        window_chars=100,
        pinned_sources=["inbox/other.md"],
    )


def test_compile_evidence_context_uses_item_boundaries_and_reports_budget_omissions(tmp_path):
    _write(tmp_path, "inbox/first.md", "target evidence one\n")
    _write(tmp_path, "inbox/second.md", "target evidence two\n")
    mk = MemKraft(str(tmp_path))

    result = mk.compile_evidence_context(
        "target evidence",
        results=[
            {"file": "inbox/first.md", "score": 1.0},
            {"file": "inbox/second.md", "score": 0.9},
        ],
        budget=18,
        window_chars=100,
    )

    assert result["estimated_tokens"] <= 18
    assert result["evidence"]
    assert result["omitted_hits"] == ["inbox/second.md"]
    assert all(item["text"].endswith("\n") for item in result["evidence"])


@pytest.mark.parametrize("bad", ["../outside.md", "/tmp/outside.md"])
def test_compile_evidence_context_never_reads_outside_base_dir(tmp_path, bad):
    mk = MemKraft(str(tmp_path))
    result = mk.compile_evidence_context("secret", results=[{"file": bad, "score": 1.0}])
    assert result["evidence"] == []
    assert result["sources"] == []


def test_compile_evidence_context_keeps_match_in_a_long_window_and_merges_overlap(tmp_path):
    _write(
        tmp_path,
        "inbox/long.md",
        "A" * 100 + " target proof\n"
        "target one\nmiddle\ntarget two\n",
    )
    mk = MemKraft(str(tmp_path))

    result = mk.compile_evidence_context(
        "target",
        results=[{"file": "inbox/long.md", "score": 1.0}],
        budget=200,
        window_chars=20,
        adjacent_windows=1,
    )

    assert all("target" in item["text"].lower() for item in result["evidence"])
    # Item spans never exceed the configured cap, even when nearby evidence
    # cannot be merged without violating that cap.
    assert all(len(item["text"]) <= 20 for item in result["evidence"])
    assert len({tuple(item["span"]) for item in result["evidence"]}) == len(result["evidence"])


def test_compile_evidence_context_accepts_single_character_queries_and_escapes_headers(tmp_path):
    _write(tmp_path, "inbox/a\nignored.md", "Q appears here.\n")
    mk = MemKraft(str(tmp_path))

    result = mk.compile_evidence_context(
        "Q",
        results=[{"file": "inbox/a\nignored.md", "score": 1.0}],
        budget=100,
    )

    assert result["evidence"][0]["text"] == "Q appears here.\n"
    assert '"file":"inbox/a\\nignored.md"' in result["text"]


def test_compile_evidence_context_ignores_function_words_when_selecting_evidence(tmp_path):
    _write(
        tmp_path,
        "inbox/session.md",
        "I do routine chores every day.\n"
        "You have a meeting tomorrow.\n"
        "You stop checking work emails and messages by 7 pm.\n"
        "What do you have planned later?\n",
    )
    result = MemKraft(str(tmp_path)).compile_evidence_context(
        "What time do I stop checking work emails and messages?",
        results=[{"file": "inbox/session.md", "score": 1.0}],
        budget=100,
        window_chars=120,
        adjacent_windows=0,
    )

    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["text"] == "You stop checking work emails and messages by 7 pm.\n"


def test_compile_evidence_context_keeps_numeric_facts_for_quantitative_cross_hit_reasoning(tmp_path):
    _write(tmp_path, "inbox/rachel.md", "Rachel gets married next year.\n")
    _write(tmp_path, "inbox/profile.md", "32\n")
    result = MemKraft(str(tmp_path)).compile_evidence_context(
        "How many years will I be when Rachel gets married?",
        results=[
            {"file": "inbox/rachel.md", "score": 1.0},
            {"file": "inbox/profile.md", "score": 0.8},
        ],
        budget=100,
        window_chars=120,
        adjacent_windows=0,
    )

    assert [item["text"] for item in result["evidence"]] == [
        "Rachel gets married next year.\n",
        "32\n",
    ]


def test_compile_evidence_context_covers_multiple_matches_on_one_long_line(tmp_path):
    _write(tmp_path, "inbox/line.md", "alpha " + "x" * 80 + " omega\n")
    mk = MemKraft(str(tmp_path))

    result = mk.compile_evidence_context(
        "alpha omega",
        results=[{"file": "inbox/line.md", "score": 1.0}],
        budget=100,
        window_chars=20,
        adjacent_windows=0,
    )

    assert any("alpha" in item["text"] for item in result["evidence"])
    assert any("omega" in item["text"] for item in result["evidence"])
    assert all(len(item["text"]) <= 20 for item in result["evidence"])


def test_compile_evidence_context_projects_nonserializable_hit_metadata(tmp_path):
    _write(tmp_path, "inbox/a.md", "target proof\n")
    result = MemKraft(str(tmp_path)).compile_evidence_context(
        "target",
        results=[{"file": "inbox/a.md", "metadata": object(), 2: "ignored"}],
        budget=100,
    )

    assert result["evidence"][0]["hit"] == {"file": "inbox/a.md"}
    assert len(result["usage_id"]) == 64


def test_compile_evidence_context_rejects_nonfinite_hit_numbers_from_json_output(tmp_path):
    _write(tmp_path, "inbox/a.md", "target proof\n")
    result = MemKraft(str(tmp_path)).compile_evidence_context(
        "target",
        results=[{"file": "inbox/a.md", "score": float("nan"), "other": float("inf")}],
        budget=100,
    )

    assert result["evidence"][0]["score"] == 0.0
    assert result["evidence"][0]["hit"] == {"file": "inbox/a.md"}
    assert json.loads(result["text"])["text"] == "target proof\n"


def test_compile_evidence_context_normalizes_unreasonably_large_scores(tmp_path):
    _write(tmp_path, "inbox/a.md", "target proof\n")
    result = MemKraft(str(tmp_path)).compile_evidence_context(
        "target",
        results=[{"file": "inbox/a.md", "score": 10 ** 10000}],
        budget=100,
    )

    assert result["evidence"][0]["score"] == 0.0
    assert result["evidence"][0]["hit"] == {"file": "inbox/a.md"}


@pytest.mark.parametrize("kwargs", [
    {"query": " "},
    {"query": "q", "budget": 0},
    {"query": "q", "top_k": True},
    {"query": "q", "adjacent_windows": -1},
    {"query": "q", "results": {}},
])
def test_compile_evidence_context_validates_contract(tmp_path, kwargs):
    with pytest.raises((TypeError, ValueError)):
        MemKraft(str(tmp_path)).compile_evidence_context(**kwargs)
