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


def _temporal_files(tmp_path, query, hits, *, budget=18):
    result = MemKraft(str(tmp_path)).compile_evidence_context(
        query,
        results=hits,
        budget=budget,
        window_chars=100,
        adjacent_windows=0,
    )
    return result, [item["file"] for item in result["evidence"]]


def test_temporal_latest_picks_newest_under_one_item_budget(tmp_path):
    _write(tmp_path, "old.md", "**Date:** 2022-01-01\nProject status old.\n")
    _write(tmp_path, "new.md", "**Date:** 2025-01-01\nProject status new.\n")

    result, files = _temporal_files(
        tmp_path,
        "What is the latest project status?",
        [{"file": "old.md"}, {"file": "new.md"}],
    )

    assert files == ["new.md"]
    assert result["evidence"][0]["hit_rank"] == 1


def test_temporal_past_picks_earliest_under_one_item_budget(tmp_path):
    _write(tmp_path, "new.md", "**Date:** 2025-01-01\nProject status new.\n")
    _write(tmp_path, "old.md", "**Date:** 2022-01-01\nProject status old.\n")

    _, files = _temporal_files(
        tmp_path,
        "What was the previous project status?",
        [{"file": "new.md"}, {"file": "old.md"}],
    )

    assert files == ["old.md"]


def test_temporal_compare_prioritizes_newest_then_earliest_before_middle(tmp_path):
    for name, date in (("middle.md", "2023-01-01"), ("old.md", "2021-01-01"), ("new.md", "2025-01-01")):
        _write(tmp_path, name, f"**Date:** {date}\nProject status {name}.\n")

    _, files = _temporal_files(
        tmp_path,
        "How did the project status change over time?",
        [{"file": "middle.md"}, {"file": "old.md"}, {"file": "new.md"}],
        budget=200,
    )

    assert files == ["new.md", "old.md", "middle.md"]


def test_temporal_explicit_valid_from_beats_markdown_date(tmp_path):
    _write(tmp_path, "explicit.md", "**Date:** 2020-01-01\nProject status explicit.\n")
    _write(tmp_path, "markdown.md", "**Date:** 2024-01-01\nProject status markdown.\n")

    _, files = _temporal_files(
        tmp_path,
        "What is the latest project status?",
        [
            {"file": "explicit.md", "valid_from": "2026-01-01"},
            {"file": "markdown.md"},
        ],
        budget=30,
    )

    assert files == ["explicit.md"]


def test_temporal_neutral_query_preserves_supplied_order_and_results(tmp_path):
    _write(tmp_path, "old.md", "**Date:** 2020-01-01\nProject status old.\n")
    _write(tmp_path, "new.md", "**Date:** 2025-01-01\nProject status new.\n")
    hits = [{"file": "old.md", "score": 0.8}, {"file": "new.md", "score": 0.9}]
    original = [dict(hit) for hit in hits]

    _, files = _temporal_files(tmp_path, "What is the project status?", hits, budget=200)

    assert files == ["old.md", "new.md"]
    assert hits == original


def test_temporal_newer_unanchored_hit_is_excluded(tmp_path):
    _write(tmp_path, "anchored.md", "**Date:** 2023-01-01\nProject status stable.\n")
    _write(tmp_path, "unanchored.md", "**Date:** 2026-01-01\nCompletely unrelated note.\n")

    _, files = _temporal_files(
        tmp_path,
        "What is the latest project status?",
        [{"file": "unanchored.md"}, {"file": "anchored.md"}],
    )

    assert files == ["anchored.md"]


def test_temporal_selection_is_deterministic_and_hard_budget_bounded(tmp_path):
    for name, date in (("undated.md", None), ("new.md", "2025-01-01"), ("old.md", "2020-01-01")):
        prefix = f"**Date:** {date}\n" if date else ""
        _write(tmp_path, name, prefix + f"Project status {name}.\n")
    hits = [{"file": "undated.md"}, {"file": "new.md"}, {"file": "old.md"}]
    mk = MemKraft(str(tmp_path))

    first = mk.compile_evidence_context(
        "What is the latest project status?", results=hits, budget=25,
        window_chars=100, adjacent_windows=0,
    )
    second = mk.compile_evidence_context(
        "What is the latest project status?", results=hits, budget=25,
        window_chars=100, adjacent_windows=0,
    )

    assert first == second
    assert first["estimated_tokens"] <= first["budget"]
    assert [item["file"] for item in first["evidence"]][0] == "new.md"


def test_temporal_undated_hits_fall_back_to_supplied_order(tmp_path):
    _write(tmp_path, "first.md", "Project status first.\n")
    _write(tmp_path, "second.md", "Project status second.\n")

    _, files = _temporal_files(
        tmp_path,
        "What is the latest project status?",
        [{"file": "first.md"}, {"file": "second.md"}],
        budget=200,
    )

    assert files == ["first.md", "second.md"]


@pytest.mark.parametrize("invalid", ["2025-99-99", "2025-01-01T25:61:00"])
def test_temporal_invalid_explicit_valid_from_falls_back_to_valid_markdown_date(tmp_path, invalid):
    _write(tmp_path, "fallback.md", "**Date:** 2026-01-01\nProject status fallback.\n")
    _write(tmp_path, "other.md", "**Date:** 2025-01-01\nProject status other.\n")
    _, files = _temporal_files(
        tmp_path, "What is the latest project status?",
        [{"file": "other.md"}, {"file": "fallback.md", "valid_from": invalid}], budget=200,
    )
    assert files == ["fallback.md", "other.md"]


def test_temporal_invalid_explicit_and_markdown_dates_are_undated(tmp_path):
    _write(tmp_path, "invalid.md", "**Date:** 2026-99-99\nProject status invalid.\n")
    _write(tmp_path, "valid.md", "**Date:** 2025-01-01\nProject status valid.\n")
    _, files = _temporal_files(
        tmp_path, "What is the latest project status?",
        [{"file": "invalid.md", "valid_from": "2027-01-01T99:00:00"}, {"file": "valid.md"}],
        budget=200,
    )
    assert files == ["valid.md", "invalid.md"]


def test_temporal_timezone_offsets_sort_by_absolute_instant(tmp_path):
    _write(tmp_path, "lexically-newer.md", "Project status offset.\n")
    _write(tmp_path, "actually-newer.md", "Project status UTC.\n")
    _, files = _temporal_files(
        tmp_path, "What is the latest project status?",
        [
            {"file": "lexically-newer.md", "valid_from": "2025-01-02T00:00:00+14:00"},
            {"file": "actually-newer.md", "valid_from": "2025-01-01T12:00:00Z"},
        ], budget=200,
    )
    assert files == ["actually-newer.md", "lexically-newer.md"]


def test_temporal_date_only_and_naive_datetime_use_deterministic_utc(tmp_path):
    _write(tmp_path, "date.md", "Project status date.\n")
    _write(tmp_path, "datetime.md", "Project status datetime.\n")
    _, files = _temporal_files(
        tmp_path, "What is the latest project status?",
        [
            {"file": "date.md", "valid_from": "2025-01-01"},
            {"file": "datetime.md", "valid_from": "2025-01-01T00:00:01"},
        ], budget=200,
    )
    assert files == ["datetime.md", "date.md"]


def test_temporal_control_terms_alone_are_not_semantic_query_anchors(tmp_path):
    _write(tmp_path, "control-only.md", "**Date:** 2026-01-01\nLatest current previous history.\n")
    _write(tmp_path, "anchored.md", "**Date:** 2025-01-01\nProject status stable.\n")
    _, files = _temporal_files(
        tmp_path, "What is the latest project status?",
        [{"file": "control-only.md"}, {"file": "anchored.md"}], budget=200,
    )
    assert files == ["anchored.md"]

    _, control_only_files = _temporal_files(
        tmp_path, "Latest current previous history",
        [{"file": "control-only.md"}], budget=200,
    )
    assert control_only_files == []


@pytest.mark.parametrize(
    "query, first_text, second_text",
    [
        ("What current does the charger provide?", "Charger current is 2 amps.\n", "Charger current is 3 amps.\n"),
        ("What is the history of the museum?", "Museum history begins here.\n", "Museum history continues here.\n"),
    ],
)
def test_temporal_ambiguous_current_and_generic_history_preserve_hit_order(
    tmp_path, query, first_text, second_text
):
    _write(tmp_path, "first.md", "**Date:** 2020-01-01\n" + first_text)
    _write(tmp_path, "second.md", "**Date:** 2025-01-01\n" + second_text)
    _, files = _temporal_files(
        tmp_path, query, [{"file": "first.md"}, {"file": "second.md"}], budget=200,
    )
    assert files == ["first.md", "second.md"]


@pytest.mark.parametrize("query", [
    "What is the current draw?",
    "What is the current rating?",
    "What is the current consumption?",
    "What current voltage is reported?",
])
def test_electrical_current_queries_are_temporally_neutral(tmp_path, query):
    text1 = "**Date:** 2020-01-01\nCurrent draw rating consumption voltage is 2 amps.\n"
    text2 = "**Date:** 2025-01-01\nCurrent draw rating consumption voltage is 3 amps.\n"
    _write(tmp_path, "first.md", text1)
    _write(tmp_path, "second.md", text2)
    _, files = _temporal_files(
        tmp_path, query, [{"file": "first.md"}, {"file": "second.md"}], budget=200,
    )
    assert files == ["first.md", "second.md"]


@pytest.mark.parametrize("query", [
    "How much current does the charger provide?",
    "How much current can the adapter output?",
    "How much current will flow from the supply?",
])
def test_electrical_quantity_current_with_transfer_verbs_is_temporally_neutral(tmp_path, query):
    text1 = "**Date:** 2020-01-01\nThe charger adapter supply can provide output flow current of 2 amps.\n"
    text2 = "**Date:** 2025-01-01\nThe charger adapter supply can provide output flow current of 3 amps.\n"
    _write(tmp_path, "first.md", text1)
    _write(tmp_path, "second.md", text2)
    _, files = _temporal_files(
        tmp_path, query, [{"file": "first.md"}, {"file": "second.md"}], budget=200,
    )
    assert files == ["first.md", "second.md"]


def test_current_status_still_selects_latest_evidence(tmp_path):
    _write(tmp_path, "first.md", "**Date:** 2020-01-01\nService status is starting.\n")
    _write(tmp_path, "second.md", "**Date:** 2025-01-01\nService status is healthy.\n")
    _, files = _temporal_files(
        tmp_path, "What is the current service status?",
        [{"file": "first.md"}, {"file": "second.md"}], budget=200,
    )
    assert files == ["second.md", "first.md"]


def test_temporal_utc_normalization_overflow_falls_back_without_crashing(tmp_path):
    _write(tmp_path, "overflow.md", "**Date:** 2026-01-01\nProject status overflow fallback.\n")
    _write(tmp_path, "other.md", "**Date:** 2025-01-01\nProject status other.\n")
    _, files = _temporal_files(
        tmp_path, "What is the latest project status?",
        [
            {"file": "other.md"},
            {"file": "overflow.md", "valid_from": "0001-01-01T00:00:00+14:00"},
        ], budget=200,
    )
    assert files == ["overflow.md", "other.md"]


def test_markdown_source_date_prefix_allows_trailing_prose(tmp_path):
    _write(tmp_path, "older.md", "**Date:** 2025-01-01\nProject status older.\n")
    _write(
        tmp_path,
        "newer.md",
        "**Date:** 2026-01-01 Release notes for this source\nProject status newer.\n",
    )

    _, files = _temporal_files(
        tmp_path,
        "What is the latest project status?",
        [{"file": "older.md"}, {"file": "newer.md"}],
        budget=200,
    )

    assert files == ["newer.md", "older.md"]


@pytest.mark.parametrize("timestamp", [
    "2026-01-01T01:02:03Z",
    "2026-01-01 01:02:03+09:00",
    "2026-01-01T01:02:03.456-05:30",
])
def test_markdown_source_date_keeps_supported_timezone_forms(tmp_path, timestamp):
    _write(tmp_path, "older.md", "**Date:** 2025-01-01\nProject status older.\n")
    _write(tmp_path, "newer.md", f"**Date:** {timestamp} trailing prose\nProject status newer.\n")

    _, files = _temporal_files(
        tmp_path,
        "What is the latest project status?",
        [{"file": "older.md"}, {"file": "newer.md"}],
        budget=200,
    )

    assert files == ["newer.md", "older.md"]
