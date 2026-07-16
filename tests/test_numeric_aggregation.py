from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from memkraft import MemKraft


def _write(base: Path, name: str, text: str) -> None:
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _mk(tmp_path, files):
    for name, text in files.items():
        _write(tmp_path, name, text)
    return MemKraft(str(tmp_path))


def test_sum_same_unit_preserves_audit_order_and_verbatim_provenance(tmp_path):
    mk = _mk(tmp_path, {
        "sessions/a.md": "Hotel cost: USD 12.50\n",
        "sessions/b.md": "Hotel cost: $7.25\n",
    })
    hits = [
        {"file": "sessions/b.md", "fact_id": "expense-b", "score": .9},
        {"file": "sessions/a.md", "fact_id": "expense-a", "score": .8},
    ]
    result = mk.aggregate_numeric_evidence("What is the total hotel cost?", results=hits)

    assert result["operation"] == "sum"
    assert result["status"] == "ok"
    assert result["value"] == Decimal("19.75")
    assert result["unit"] == "USD"
    assert [fact["fact_id"] for fact in result["facts"]] == ["expense-b", "expense-a"]
    for fact in result["facts"]:
        source = (tmp_path / fact["file"]).read_text(encoding="utf-8")
        start, end = fact["span"]
        assert fact["text"] == source[start:end]


def test_deterministic_results_immutable_and_input_validation(tmp_path):
    mk = _mk(tmp_path, {"a.md": "Coffee cost USD 2\n"})
    hits = [{"file": "a.md", "fact_id": "x", "metadata": {"n": 1}}]
    original = deepcopy(hits)
    first = mk.aggregate_numeric_evidence("sum coffee cost", results=hits, top_k=3)
    assert first == mk.aggregate_numeric_evidence("sum coffee cost", results=hits, top_k=3)
    assert hits == original
    with pytest.raises(TypeError):
        mk.aggregate_numeric_evidence(3, results=hits)
    with pytest.raises(ValueError):
        mk.aggregate_numeric_evidence(" ", results=hits)
    with pytest.raises(TypeError):
        mk.aggregate_numeric_evidence("sum coffee", results={})
    with pytest.raises(ValueError):
        mk.aggregate_numeric_evidence("sum coffee", results=hits, top_k=True)


def test_sum_ignores_dates_versions_ids_and_unrelated_numbers(tmp_path):
    mk = _mk(tmp_path, {"a.md": (
        "Release date 2025-01-17, version 3.0.1, build ID 98765.\n"
        "Coffee cost: USD 4.50\n"
        "Unrelated parking cost: USD 99\n"
    )})
    result = mk.aggregate_numeric_evidence(
        "What is the total coffee cost?", results=[{"file": "a.md", "fact_id": "coffee"}]
    )
    assert result["status"] == "ok"
    assert result["value"] == Decimal("4.50")
    assert result["unit"] == "USD"
    assert result["excluded"]


def test_multiple_quantities_on_one_relevant_line_is_ambiguous(tmp_path):
    mk = _mk(tmp_path, {"a.md": "Hotel cost USD 10 plus tax USD 2\n"})
    result = mk.aggregate_numeric_evidence("total hotel cost", results=[{"file": "a.md"}])
    assert result["status"] == "ambiguous"
    assert result["value"] is None


@pytest.mark.parametrize("texts", [
    ["Hotel cost USD 10\n", "Hotel cost EUR 5\n"],
    ["Cargo weight 10 kg\n", "Cargo weight 5 lb\n"],
    ["Coffee cost USD 10\n", "Coffee cost 5\n"],
])
def test_sum_rejects_mixed_currency_units_and_unitless_mix(tmp_path, texts):
    mk = _mk(tmp_path, {f"{i}.md": text for i, text in enumerate(texts)})
    result = mk.aggregate_numeric_evidence(
        "sum hotel cargo coffee cost weight",
        results=[{"file": f"{i}.md", "fact_id": str(i)} for i in range(len(texts))],
    )
    assert result["status"] == "ambiguous"
    assert result["value"] is None


def test_sum_canonicalizes_ml_and_rejects_mixed_metre_millilitre_units(tmp_path):
    mk = _mk(tmp_path, {
        "metres.md": "Supply amount 3 m\n",
        "millilitres.md": "Supply amount 4 ml\n",
    })

    result = mk.aggregate_numeric_evidence(
        "sum supply amount",
        results=[
            {"file": "metres.md", "fact_id": "metres"},
            {"file": "millilitres.md", "fact_id": "millilitres"},
        ],
    )

    assert result["status"] == "ambiguous"
    assert [fact["unit"] for fact in result["facts"]] == ["m", "mL"]


@pytest.mark.parametrize("word", ["million", "melons", "large"])
def test_supported_unit_prefix_is_not_taken_from_trailing_ascii_word(tmp_path, word):
    mk = _mk(tmp_path, {"a.md": f"Shipment amount 5 {word}\n"})

    result = mk.aggregate_numeric_evidence(
        "sum shipment amount", results=[{"file": "a.md", "fact_id": word}]
    )

    assert result["status"] == "ok"
    assert result["value"] == Decimal("5")
    assert result["unit"] is None


def test_fact_id_is_only_dedup_key_and_equal_distinct_facts_sum(tmp_path):
    mk = _mk(tmp_path, {
        "a.md": "Snack cost USD 3\n",
        "b.md": "Snack cost USD 3\n",
        "c.md": "Snack cost USD 3\n",
    })
    deduped = mk.aggregate_numeric_evidence("total snack cost", results=[
        {"file": "a.md", "fact_id": "same"}, {"file": "b.md", "fact_id": "same"},
        {"file": "c.md", "fact_id": "different"},
    ])
    assert deduped["status"] == "ok"
    assert deduped["value"] == Decimal("6")
    assert len(deduped["facts"]) == 2


def test_identical_cross_file_fact_without_provenance_is_ambiguous(tmp_path):
    mk = _mk(tmp_path, {"a.md": "Snack cost USD 3\n", "b.md": "Snack cost $3.00\n"})
    result = mk.aggregate_numeric_evidence(
        "total snack cost", results=[{"file": "a.md"}, {"file": "b.md"}]
    )
    assert result["status"] == "ambiguous"
    assert result["value"] is None


def test_count_counts_distinct_matching_event_facts_not_digits(tmp_path):
    mk = _mk(tmp_path, {
        "a.md": "Deployed service on 2025-01-01 as version 2.4, build ID 900.\n",
        "b.md": "Deployed service on 2025-02-01 as version 3.0, build ID 901.\n",
        "noise.md": "Bought 12 apples.\n",
    })
    result = mk.aggregate_numeric_evidence("How many times was service deployed?", results=[
        {"file": "a.md", "fact_id": "deploy-a"},
        {"file": "b.md", "fact_id": "deploy-b"},
        {"file": "noise.md", "fact_id": "apples"},
    ])
    assert result["operation"] == "count"
    assert result["status"] == "ok"
    assert result["value"] == Decimal("2")
    assert result["unit"] == "events"


def test_duration_requires_exactly_two_explicit_iso_date_endpoints(tmp_path):
    mk = _mk(tmp_path, {"trip.md": "Trip started 2025-01-01 and ended 2025-01-11.\n"})
    ok = mk.aggregate_numeric_evidence(
        "How long was the trip?", results=[{"file": "trip.md", "fact_id": "trip"}]
    )
    assert ok["operation"] == "duration"
    assert ok["status"] == "ok"
    assert ok["value"] == Decimal("10")
    assert ok["unit"] == "days"

    _write(tmp_path, "many.md", "Trip dates: 2025-01-01, 2025-01-05, 2025-01-11.\n")
    many = mk.aggregate_numeric_evidence("trip duration", results=[{"file": "many.md"}])
    assert many["status"] == "ambiguous"
    assert many["value"] is None


def test_missing_unreadable_or_path_escape_is_incomplete_without_partial_value(tmp_path):
    mk = _mk(tmp_path, {"ok.md": "Coffee cost USD 4\n"})
    for bad in ("missing.md", "../outside.md", "/tmp/outside.md"):
        result = mk.aggregate_numeric_evidence(
            "total coffee cost", results=[{"file": "ok.md", "fact_id": "ok"}, {"file": bad}]
        )
        assert result["status"] == "incomplete"
        assert result["value"] is None
        assert result["reason"]


def test_requires_explicit_supported_intent_and_preserves_evidence_api_shape(tmp_path):
    mk = _mk(tmp_path, {"a.md": "Coffee cost USD 4\n"})
    unsupported = mk.aggregate_numeric_evidence("What was the coffee cost?", results=[{"file": "a.md"}])
    assert unsupported["operation"] is None
    assert unsupported["status"] == "unsupported"
    assert unsupported["value"] is None
    assert set(unsupported) == {"operation", "status", "value", "unit", "facts", "excluded", "reason", "scope"}

    evidence = mk.compile_evidence_context("coffee", results=[{"file": "a.md"}], budget=100)
    assert set(evidence) == {
        "query", "budget", "estimated_tokens", "text", "evidence", "sources", "omitted_hits", "usage_id"
    }
    assert set(evidence["evidence"][0]) == {"file", "span", "text", "score", "hit_rank", "hit"}


def test_unsupported_derived_numeric_operations_are_not_inferred(tmp_path):
    mk = _mk(tmp_path, {"a.md": "Widget unit price USD 4 and quantity 3\n"})
    for query in ("average widget price", "widget price ratio", "convert widget price", "widget unit price times quantity"):
        result = mk.aggregate_numeric_evidence(query, results=[{"file": "a.md"}])
        assert result["status"] == "unsupported"
        assert result["value"] is None


def test_same_fact_id_only_deduplicates_identical_fact_signature(tmp_path):
    mk = _mk(tmp_path, {"a.md": "Refund total USD -5\n", "b.md": "Refund total USD +5\n"})
    result = mk.aggregate_numeric_evidence("sum refund total", results=[
        {"file": "a.md", "fact_id": "refund", "valid_from": "2025-01-01"},
        {"file": "b.md", "fact_id": "refund", "valid_from": "2025-01-02"},
    ])
    assert result["status"] == "ambiguous"
    assert len(result["facts"]) == 2


@pytest.mark.parametrize("line, expected, unit", [
    ("Adjustment USD -5.25\n", Decimal("-5.25"), "USD"),
    ("Adjustment +5 kg\n", Decimal("5"), "kg"),
    ("Adjustment -$5\n", Decimal("-5"), "USD"),
])
def test_signed_decimal_quantities_preserve_sign_and_unit(tmp_path, line, expected, unit):
    mk = _mk(tmp_path, {"a.md": line})
    result = mk.aggregate_numeric_evidence("sum adjustment", results=[{"file": "a.md", "fact_id": "a"}])
    assert result["status"] == "ok"
    assert result["value"] == expected
    assert result["unit"] == unit


def test_count_rejects_entity_and_status_rows_conservatively(tmp_path):
    mk = _mk(tmp_path, {"events.md": "Service alpha\nService status: deployed\n"})
    result = mk.aggregate_numeric_evidence(
        "How many times was service deployed?", results=[{"file": "events.md", "fact_id": "hit"}],
    )
    assert result["status"] in {"ambiguous", "unsupported"}
    assert result["value"] is None


def test_count_does_not_merge_distinct_event_lines_sharing_hit_fact_id(tmp_path):
    mk = _mk(tmp_path, {"events.md": (
        "Deployed service on 2025-01-01.\nDeployed service on 2025-02-01.\n"
    )})
    result = mk.aggregate_numeric_evidence(
        "How many times was service deployed?", results=[{"file": "events.md", "fact_id": "hit"}],
    )
    assert result["status"] == "ok"
    assert result["value"] == Decimal("2")


def test_duration_facts_preserve_each_endpoint_exact_provenance(tmp_path):
    text = "Trip started 2025-01-01 and ended 2025-01-11.\n"
    mk = _mk(tmp_path, {"trip.md": text})
    result = mk.aggregate_numeric_evidence("trip duration", results=[{"file": "trip.md", "fact_id": "trip"}])
    assert result["status"] == "ok"
    assert [fact["text"] for fact in result["facts"]] == ["2025-01-01", "2025-01-11"]
    assert [fact["date"] for fact in result["facts"]] == ["2025-01-01", "2025-01-11"]
    for fact in result["facts"]:
        start, end = fact["span"]
        assert text[start:end] == fact["text"]


def test_count_requires_query_event_predicate_in_each_counted_line(tmp_path):
    mk = _mk(tmp_path, {"events.md": (
        "Service deployed on 2025-01-01.\n"
        "Service restarted on 2025-01-02.\n"
    )})

    result = mk.aggregate_numeric_evidence(
        "How many times was service deployed?", results=[{"file": "events.md", "fact_id": "events"}],
    )

    assert result["status"] == "ok"
    assert result["value"] == Decimal("1")
    assert len(result["facts"]) == 1
    assert "deployed" in result["facts"][0]["text"].lower()
    assert any("restarted" in item["text"].lower() for item in result["excluded"])


def test_duration_same_date_endpoints_preserve_distinct_spans(tmp_path):
    text = "Maintenance started 2025-01-01 and ended 2025-01-01.\n"
    mk = _mk(tmp_path, {"maintenance.md": text})

    result = mk.aggregate_numeric_evidence(
        "maintenance duration", results=[{"file": "maintenance.md", "fact_id": "maintenance"}],
    )

    assert result["status"] == "ok"
    assert result["value"] == Decimal("0")
    assert len(result["facts"]) == 2
    assert result["facts"][0]["span"] != result["facts"][1]["span"]


def test_mixin_wiring_preserves_legacy_extensions_and_guards_new_mixins():
    import inspect
    import memkraft
    from memkraft.lifecycle import LifecycleMixin

    # LifecycleMixin intentionally extends the old core health() alias.  Legacy
    # mixin precedence is public, shipped behavior and must remain intact.
    assert memkraft.MemKraft.health is LifecycleMixin.health

    # Newly introduced mixins are additive-only and must not replace an API
    # already installed by core or a legacy mixin.
    source = inspect.getsource(memkraft)
    assert "_ADDITIVE_ONLY_MIXINS" in source
    assert "if _mixin in _ADDITIVE_ONLY_MIXINS and hasattr(_BaseMemKraft, _name):" in source
    assert "and not getattr(_mixin, _name) is _attr" not in source

    assert hasattr(memkraft.MemKraft, "aggregate_numeric_evidence")
    assert hasattr(memkraft.MemKraft, "compile_evidence_context")
