"""Evaluation Corpus v0 — runtime-neutral reusable eval cases.

The corpus stores *what to test* and *what someone observed*, never the raw
conversation. Every field is an id, a closed enum, a bounded string, or an
opaque ref core stores and compares but never fetches. Nothing here executes an
evaluator, and no run result touches the improvement ledger's promotion state.
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from memkraft import MemKraft, store_core
from memkraft.execution_protocol import ExecutionError


NOW = "2026-08-08T09:00:00Z"
DIGEST_A = "a" * 64

CASE = dict(
    case_id="case.recency-tie",
    failure_type="stale_recall",
    anonymized_input_ref="corpus://anon/0001",
    expected_behavior="recall the newest matching memory first",
    forbidden_behavior="return the superseded memory as current",
    trace_ref="trace://run/0001",
    source_profile="profile.support-agent",
    privacy_scope="local_private",
)


def _mk(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    return mk


def _log_path(mk) -> Path:
    return Path(mk.base_dir) / ".memkraft" / "evaluation" / "events.jsonl"


def _records(mk):
    return store_core.read_all(_log_path(mk)).records


def _register(mk, **overrides):
    args = dict(CASE)
    args.update(overrides)
    return mk.evaluation_register_case(now=NOW, **args)


def _label(mk, **overrides):
    args = dict(case_id=CASE["case_id"], label="fail",
                evidence_ref="note://reviewer/7")
    args.update(overrides)
    return mk.evaluation_add_label(now=NOW, **args)


def _run(mk, **overrides):
    args = dict(case_id=CASE["case_id"], evaluator_kind="offline_replay",
                evaluator_version="2026.08.1", verdict="pass",
                candidate_digest=DIGEST_A, evidence_ref="report://run/9")
    args.update(overrides)
    return mk.evaluation_record_run(now=NOW, **args)


# -- registration ------------------------------------------------------------

def test_register_case_stores_schema_fields_and_no_raw_content(tmp_path):
    mk = _mk(tmp_path)
    result = _register(mk)

    assert result["outcome"] == "applied"
    record = result["record"]
    assert record["record_type"] == "evaluation_case"
    assert record["case_id"] == CASE["case_id"]
    assert record["failure_type"] == "stale_recall"
    assert record["privacy_scope"] == "local_private"
    assert record["authority_verified"] is False
    assert record["event_seq"] == 1

    # No raw-content channel exists: the schema is closed, and the only
    # free-text fields are the bounded behavior contracts.
    forbidden = {"prompt", "conversation", "body", "text", "content",
                 "messages", "transcript", "input", "raw_input"}
    assert forbidden.isdisjoint(record)


@pytest.mark.parametrize("failure_type", [
    "user_correction", "delegated_failed", "delegated_partial",
    "delegated_timed_out", "tool_error", "routing_error",
    "unverified_completion",
])
def test_register_case_accepts_hermes_eval_bridge_failure_types(
        tmp_path, failure_type):
    mk = _mk(tmp_path)
    result = _register(mk, failure_type=failure_type)
    assert result["record"]["failure_type"] == failure_type


def test_register_case_accepts_opaque_correction_trace_ref(tmp_path):
    mk = _mk(tmp_path)
    result = _register(
        mk, failure_type="user_correction",
        trace_ref="correction://corr.alpha/r2",
    )
    assert result["record"]["trace_ref"] == "correction://corr.alpha/r2"


def test_register_case_rejects_raw_content_kwargs(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(TypeError):
        _register(mk, conversation="user: hello")


def test_register_case_validates_id_grammar_and_privacy_enum(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as bad_id:
        _register(mk, case_id="Case With Spaces")
    assert bad_id.value.code == "E_EVALCORPUS_PATTERN"

    with pytest.raises(ExecutionError) as bad_privacy:
        _register(mk, privacy_scope="public")
    assert bad_privacy.value.code == "E_EVALCORPUS_PATTERN"


def test_register_case_bounds_strings(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as error:
        _register(mk, expected_behavior="x" * 513)
    assert error.value.code == "E_EVALCORPUS_VALIDATION"


def test_duplicate_case_id_fails(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    with pytest.raises(ExecutionError) as error:
        _register(mk, trace_ref="trace://run/0002")
    assert error.value.code == "E_EVALCORPUS_ALREADY_EXISTS"


def test_exact_retry_under_one_operation_id_is_idempotent(tmp_path):
    mk = _mk(tmp_path)
    first = _register(mk, operation_id="op-1")
    second = _register(mk, operation_id="op-1")

    assert second["outcome"] == "already_applied"
    assert second["record_id"] == first["record_id"]
    assert len(_records(mk)) == 1


def test_changed_payload_under_same_operation_id_fails_closed(tmp_path):
    mk = _mk(tmp_path)
    _register(mk, operation_id="op-1")
    with pytest.raises(ExecutionError) as error:
        _register(mk, operation_id="op-1", case_id="case.other")
    assert error.value.code == "E_EVALCORPUS_IDEMPOTENCY_MISMATCH"
    assert len(_records(mk)) == 1


# -- human labels ------------------------------------------------------------

def test_label_records_human_authority_without_claiming_verification(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    record = _label(mk)["record"]

    assert record["record_type"] == "evaluation_label"
    assert record["label"] == "fail"
    assert record["authority_claim"] == "human"
    assert record["authority_verified"] is False
    assert record["evidence_ref"] == "note://reviewer/7"


def test_label_rejects_unknown_case_and_bad_label(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as unknown:
        _label(mk)
    assert unknown.value.code == "E_EVALCORPUS_NOT_FOUND"

    _register(mk)
    with pytest.raises(ExecutionError) as bad:
        _label(mk, label="maybe")
    assert bad.value.code == "E_EVALCORPUS_PATTERN"


def test_labels_accumulate_and_never_overwrite(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    _label(mk, label="fail")
    _label(mk, label="pass")

    case = mk.evaluation_project(now=NOW)["cases"][CASE["case_id"]]
    assert [entry["label"] for entry in case["labels"]] == ["fail", "pass"]


# -- external run results ----------------------------------------------------

def test_run_result_is_stored_verbatim_without_execution(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    record = _run(mk)["record"]

    assert record["record_type"] == "evaluation_run"
    assert record["evaluator_kind"] == "offline_replay"
    assert record["evaluator_version"] == "2026.08.1"
    assert record["verdict"] == "pass"
    assert record["candidate_digest"] == DIGEST_A
    assert record["authority_verified"] is False


def test_run_rejects_unknown_case_and_malformed_digest(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as unknown:
        _run(mk)
    assert unknown.value.code == "E_EVALCORPUS_NOT_FOUND"

    _register(mk)
    with pytest.raises(ExecutionError) as bad:
        _run(mk, candidate_digest="not-a-digest")
    assert bad.value.code == "E_EVALCORPUS_PATTERN"


def test_run_correlation_ids_do_not_touch_the_improvement_ledger(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    _run(mk, proposal_id="prop.retrieval-boost", evaluation_kind="suite-green")

    record = _records(mk)[-1]
    assert record["proposal_id"] == "prop.retrieval-boost"
    assert record["evaluation_kind"] == "suite-green"

    # Correlation only: Evaluation Corpus must not invoke or depend on the
    # Improvement Ledger, even when both additive previews are installed.
    improvement_log = (Path(mk.base_dir) / ".memkraft" / "improvement"
                       / "events.jsonl")
    assert not improvement_log.exists()
    assert getattr(mk, "improvement_project")(now=NOW) == {
        "schema": 1,
        "generated_at": NOW,
        "high_water_seq": 0,
        "skipped_lines": 0,
        "proposals": {},
        "artifacts": {},
    }
    assert not improvement_log.exists()


# -- projection --------------------------------------------------------------

def test_projection_is_deterministic_and_orders_history(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    _label(mk, label="fail")
    _run(mk, verdict="inconclusive")
    _run(mk, verdict="pass", evidence_ref="report://run/10")

    view = mk.evaluation_project(now=NOW)
    assert view["skipped_lines"] == 0
    assert view["high_water_seq"] == 4

    case = view["cases"][CASE["case_id"]]
    assert case["failure_type"] == "stale_recall"
    assert [entry["verdict"] for entry in case["runs"]] == [
        "inconclusive", "pass"
    ]
    assert [entry["event_seq"] for entry in case["runs"]] == [3, 4]

    # Same lines, replayed in reversed physical order, fold identically.
    path = _log_path(mk)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    assert dict(mk.evaluation_project(now=NOW)) == dict(view)


def test_projection_of_untouched_base_dir_creates_nothing(tmp_path):
    mk = _mk(tmp_path)
    view = mk.evaluation_project(now=NOW)
    assert view["cases"] == {}
    assert not _log_path(mk).exists()


def test_case_filter_narrows_projection(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    _register(mk, case_id="case.other", trace_ref="trace://run/0002")

    view = mk.evaluation_project(now=NOW, case_id="case.other")
    assert list(view["cases"]) == ["case.other"]


# -- corruption asymmetry ----------------------------------------------------

def test_damaged_log_stays_readable_but_refuses_every_write(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    _label(mk, label="pass")

    path = _log_path(mk)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    view = mk.evaluation_project(now=NOW)
    assert view["skipped_lines"] == 1
    assert list(view["cases"]) == [CASE["case_id"]]

    for write in (lambda: _register(mk, case_id="case.new"),
                  lambda: _label(mk, label="fail"),
                  lambda: _run(mk)):
        with pytest.raises(ExecutionError) as error:
            write()
        assert error.value.code == "E_EVALCORPUS_LOG_CORRUPT"


# -- opaque refs are not a prose channel -------------------------------------

#: Each of these is expressive enough to carry a sentence, a control byte, or a
#: bare filesystem path — the three shapes the ref grammar exists to exclude.
NOT_A_REF = [
    "the reviewer said the newest memory was returned second",
    "corpus://anon/0001 and then some prose",
    "corpus://anon/00 01",
    "corpus://anon/0001\n",
    "corpus://anon/0001\x00",
    "corpus://anon/\t0001",
    "/var/tmp/anon/0001",
    "/etc/passwd",
    "file:///etc/passwd",
    "corpus:///0001",
    "corpus://anon/../../etc/passwd",
    "corpus://anon/0001?q=hello world",
    "http://example.com/0001",
    "corpus://anon",
    "local:sha256:" + "z" * 64,
    "",
    "x" * 300,
]

#: Every ref the existing fixtures already store, which must keep working.
ACCEPTED_REFS = [
    "corpus://anon/0001",
    "trace://run/0001",
    "note://reviewer/7",
    "report://run/9",
    "local:sha256:" + "b" * 64,
    "artifact://bundle-2/v1.2.3/part_4",
]


@pytest.mark.parametrize("value", NOT_A_REF)
def test_case_refs_reject_prose_whitespace_and_filesystem_paths(
        tmp_path, value):
    mk = _mk(tmp_path)
    for field in ("anonymized_input_ref", "trace_ref"):
        with pytest.raises(ExecutionError) as error:
            _register(mk, **{field: value})
        assert error.value.code in ("E_EVALCORPUS_PATTERN",
                                    "E_EVALCORPUS_VALIDATION")
        assert error.value.details["path"] == field
    assert not _log_path(mk).exists()


@pytest.mark.parametrize("value", NOT_A_REF)
def test_evidence_refs_reject_prose_on_labels_and_runs(tmp_path, value):
    mk = _mk(tmp_path)
    _register(mk)

    with pytest.raises(ExecutionError) as label_error:
        _label(mk, evidence_ref=value)
    assert label_error.value.details["path"] == "evidence_ref"

    with pytest.raises(ExecutionError) as run_error:
        _run(mk, evidence_ref=value)
    assert run_error.value.details["path"] == "evidence_ref"

    # Rejection happens before the append: only the case is on disk.
    assert len(_records(mk)) == 1


@pytest.mark.parametrize("value", ACCEPTED_REFS)
def test_existing_opaque_refs_still_pass(tmp_path, value):
    mk = _mk(tmp_path)
    result = _register(mk, anonymized_input_ref=value, trace_ref=value)
    assert result["record"]["anonymized_input_ref"] == value
    assert result["record"]["trace_ref"] == value


def test_id_and_digest_grammars_reject_a_trailing_newline(tmp_path):
    mk = _mk(tmp_path)
    # Python's ``$`` matches before a trailing newline, so every anchored
    # grammar here has to end in ``\Z`` or a control character rides along.
    with pytest.raises(ExecutionError) as bad_case:
        _register(mk, case_id=CASE["case_id"] + "\n")
    assert bad_case.value.code == "E_EVALCORPUS_PATTERN"

    with pytest.raises(ExecutionError) as bad_profile:
        _register(mk, source_profile="profile.support-agent\n")
    assert bad_profile.value.code == "E_EVALCORPUS_PATTERN"

    _register(mk)
    with pytest.raises(ExecutionError) as bad_digest:
        _run(mk, candidate_digest=DIGEST_A + "\n")
    assert bad_digest.value.code == "E_EVALCORPUS_PATTERN"

    with pytest.raises(ExecutionError) as bad_proposal:
        _run(mk, proposal_id="prop.retrieval-boost\n")
    assert bad_proposal.value.code == "E_EVALCORPUS_PATTERN"


def test_label_has_no_free_text_keyword(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    # There is no ``notes=`` channel: the signature is closed, so prose cannot
    # arrive at all rather than arriving and being sanitized.
    with pytest.raises(TypeError):
        _label(mk, notes="the reviewer explained why this failed")
    assert len(_records(mk)) == 1


# -- corruption shapes that block every write --------------------------------

def _append_raw(mk, payload) -> None:
    """Append one line verbatim, bypassing every writer."""
    path = _log_path(mk)
    data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    with path.open("ab") as handle:
        handle.write(data + b"\n")


def _assert_writes_blocked(mk, expected_skipped) -> None:
    """Reads still work and report the damage; every writer fails closed."""
    view = mk.evaluation_project(now=NOW)
    assert view["skipped_lines"] == expected_skipped

    for write in (lambda: _register(mk, case_id="case.new"),
                  lambda: _label(mk, label="fail"),
                  lambda: _run(mk)):
        with pytest.raises(ExecutionError) as error:
            write()
        assert error.value.code == "E_EVALCORPUS_LOG_CORRUPT"
        assert error.value.details["skipped_lines"] == expected_skipped


def test_parseable_but_incomplete_case_counts_as_corrupt(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)

    # Valid JSON, right record_type, but missing ``forbidden_behavior``: half a
    # case that would look usable to anything checking only a field or two.
    partial = dict(_records(mk)[0])
    partial.pop("forbidden_behavior")
    partial["id"] = "hand-written-1"
    partial["case_id"] = "case.partial"
    partial["event_seq"] = 2
    _append_raw(mk, json.dumps(partial))

    _assert_writes_blocked(mk, 1)
    # The intact case is still projected.
    assert list(mk.evaluation_project(now=NOW)["cases"]) == [CASE["case_id"]]


def test_authority_verified_true_on_a_stored_line_is_corruption(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)

    forged = dict(_records(mk)[0])
    forged["id"] = "hand-written-1"
    forged["case_id"] = "case.forged"
    forged["event_seq"] = 2
    forged["authority_verified"] = True
    _append_raw(mk, json.dumps(forged))

    _assert_writes_blocked(mk, 1)


def test_duplicate_case_declaration_at_two_sequences_is_corruption(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)

    # Same case_id declared again at a different event_seq: two histories with
    # no basis to prefer either.
    duplicate = dict(_records(mk)[0])
    duplicate["id"] = "hand-written-1"
    duplicate["event_seq"] = 2
    duplicate["expected_behavior"] = "a different contract entirely"
    _append_raw(mk, json.dumps(duplicate))

    # Both declarations are excluded, so the case vanishes from the view while
    # the damage stays visible.
    view = mk.evaluation_project(now=NOW)
    assert view["cases"] == {}
    _assert_writes_blocked(mk, 2)


def test_duplicate_event_seq_excludes_both_and_blocks_writes(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    _label(mk, label="pass")

    clash = dict(_records(mk)[1])
    clash["id"] = "hand-written-1"
    clash["label"] = "inconclusive"
    _append_raw(mk, json.dumps(clash))

    view = mk.evaluation_project(now=NOW)
    assert view["cases"][CASE["case_id"]]["labels"] == []
    _assert_writes_blocked(mk, 2)


def test_orphan_label_and_run_are_corruption(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    _label(mk, label="pass")
    _run(mk)

    orphan_label = dict(_records(mk)[1])
    orphan_label["id"] = "hand-written-1"
    orphan_label["case_id"] = "case.never-declared"
    orphan_label["event_seq"] = 4
    orphan_label["operation_id"] = "hand-written-orphan-label"
    _append_raw(mk, json.dumps(orphan_label))

    orphan_run = dict(_records(mk)[2])
    orphan_run["id"] = "hand-written-2"
    orphan_run["case_id"] = "case.never-declared"
    orphan_run["event_seq"] = 5
    orphan_run["operation_id"] = "hand-written-orphan-run"
    _append_raw(mk, json.dumps(orphan_run))

    # The declared case keeps its own history; only the orphans are damage.
    case = mk.evaluation_project(now=NOW)["cases"][CASE["case_id"]]
    assert len(case["labels"]) == 1 and len(case["runs"]) == 1
    _assert_writes_blocked(mk, 2)


def test_backward_reference_is_corruption(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    _label(mk, label="pass")
    case, label = _records(mk)
    case["event_seq"], label["event_seq"] = 2, 1
    _log_path(mk).write_text(
        json.dumps(label) + "\n" + json.dumps(case) + "\n",
        encoding="utf-8",
    )
    view = mk.evaluation_project(now=NOW)
    assert view["cases"][CASE["case_id"]]["labels"] == []
    _assert_writes_blocked(mk, 1)


def test_duplicate_operation_id_is_corruption(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    _label(mk, label="pass")
    records = _records(mk)
    records[1]["operation_id"] = records[0]["operation_id"]
    _log_path(mk).write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    _assert_writes_blocked(mk, 2)


@pytest.mark.parametrize("event_seq", [2, 4])
def test_noncontiguous_sequence_is_corruption(tmp_path, event_seq):
    mk = _mk(tmp_path)
    _register(mk)
    record = _records(mk)[0]
    record["event_seq"] = event_seq
    _log_path(mk).write_text(json.dumps(record) + "\n", encoding="utf-8")
    _assert_writes_blocked(mk, 1)


def test_tombstone_shaped_line_is_corruption(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)

    # This log is append-only and never compacted, so nothing here writes a
    # tombstone; its presence is an outside edit, not a deletion.
    stored = _records(mk)[0]
    _append_raw(mk, json.dumps({
        "id": "hand-written-1",
        "record_type": "evaluation_case",
        "tombstone": True,
        "tombstone_of": stored["id"],
    }))

    _assert_writes_blocked(mk, 1)
    # The tombstone deletes nothing: the case is still readable.
    assert list(mk.evaluation_project(now=NOW)["cases"]) == [CASE["case_id"]]


def test_invalid_utf8_line_leaves_valid_records_readable(tmp_path):
    mk = _mk(tmp_path)
    _register(mk)
    _label(mk, label="pass")

    # ``store_core.read_all`` decodes the file as text, so this byte sequence
    # would take the whole projection with it; the byte-wise reader must not.
    _append_raw(mk, b'{"record_type": "evaluation_case", "case_id": "\xff\xfe"}')

    view = mk.evaluation_project(now=NOW)
    case = view["cases"][CASE["case_id"]]
    assert case["expected_behavior"] == CASE["expected_behavior"]
    assert [entry["label"] for entry in case["labels"]] == ["pass"]
    assert view["high_water_seq"] == 2
    _assert_writes_blocked(mk, 1)


# -- idempotency is byte-exact, not Unicode-normalized ------------------------

def test_nfc_equivalent_payload_under_one_operation_id_fails_closed(tmp_path):
    mk = _mk(tmp_path)

    # Same text after NFC, different codepoints: composed "é" versus "e" plus
    # a combining acute. MKCJSON would normalize these to one digest and answer
    # ``already_applied`` to a request that was never applied.
    composed = "recall the caf\u00e9 memory first"
    decomposed = "recall the cafe\u0301 memory first"
    assert composed != decomposed
    assert unicodedata.normalize("NFC", decomposed) == composed

    _register(mk, operation_id="op-1", expected_behavior=composed)
    with pytest.raises(ExecutionError) as error:
        _register(mk, operation_id="op-1", case_id="case.other",
                  expected_behavior=decomposed)

    assert error.value.code == "E_EVALCORPUS_IDEMPOTENCY_MISMATCH"
    assert "expected_behavior" in error.value.details["differing_keys"]
    assert (error.value.details["stored_fingerprint"]
            != error.value.details["request_fingerprint"])
    assert len(_records(mk)) == 1


def test_nfc_equivalent_ref_is_a_different_request(tmp_path):
    mk = _mk(tmp_path)
    _register(mk, operation_id="op-1")
    with pytest.raises(ExecutionError) as error:
        _register(mk, operation_id="op-1",
                  expected_behavior=CASE["expected_behavior"] + "\u0301")
    assert error.value.code == "E_EVALCORPUS_IDEMPOTENCY_MISMATCH"
