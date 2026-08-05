"""Continual Improvement Ledger — Slice 1 (plan §4, §5.1, §5.5, §6.2, §6.3, §9).

Slice 1 owns the substrate: the append path, immutable proposals, immutable
artifact revisions, idempotency, fail-closed scope, and the deliberate
asymmetry of §6.3 — a corrupt ledger stays readable but stops accepting
decisions. Every assertion goes through the public ``MemKraft`` object; the
only private thing these tests touch is the on-disk log, and only to read it or
to damage it on purpose.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from memkraft import MemKraft, store_core
from memkraft.execution_protocol import ExecutionError


NOW = "2026-08-06T09:00:00Z"

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64

PROPOSAL = dict(
    proposal_id="prop.retrieval-boost",
    artifact_id="artifact.search-prompt",
    summary="Raise the recency boost",
    rationale="Recent memories lost to lexical ties in three runs",
    candidate_digest=DIGEST_A,
)


def _mk(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    return mk


def _log_path(mk) -> Path:
    return Path(mk.base_dir) / ".memkraft" / "improvement" / "events.jsonl"


def _lines(mk):
    path = _log_path(mk)
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _records(mk):
    return store_core.read_all(_log_path(mk)).records


def _propose(mk, **overrides):
    args = dict(PROPOSAL)
    args.setdefault("required_evaluations", ["suite-green"])
    args.update(overrides)
    return mk.improvement_propose(now=NOW, **args)


# --------------------------------------------------------------------------
# Append path, sequencing, and the no-goal_id rule (§4, A16, A17)
# --------------------------------------------------------------------------

def test_slice1_proposal_append_allocates_the_first_sequence_number(tmp_path):
    mk = _mk(tmp_path)
    result = _propose(mk)

    assert result["outcome"] == "applied"
    assert result["event_seq"] == 1
    assert len(_lines(mk)) == 1

    record = _records(mk)[0]
    assert record["record_type"] == "improvement_proposal"
    assert record["improvement_schema"] == 1
    assert record["schema_version"] == 1
    assert record["emitted_at"] == NOW
    assert record["privacy"] == "local_private"
    assert record["authority_claim"] == "agent"
    assert record["authority_verified"] is False
    assert record["scope"] == "project"
    assert record["proposal_id"] == PROPOSAL["proposal_id"]
    assert record["candidate_digest"] == DIGEST_A
    assert record["required_evaluations"] == ["suite-green"]
    assert record["event_seq"] == 1
    assert record["id"] == result["record_id"]


def test_slice1_improvement_records_never_carry_a_goal_id(tmp_path):
    """A17: improvement lineage is not goal-scoped, structurally."""
    mk = _mk(tmp_path)
    _propose(mk)
    mk.artifact_register_revision(
        "artifact.search-prompt", "r1", DIGEST_A, now=NOW,
    )
    for record in _records(mk):
        assert "goal_id" not in record


def test_slice1_event_seq_is_monotonic_across_families(tmp_path):
    """A16: one log, one strictly increasing sequence."""
    mk = _mk(tmp_path)
    first = _propose(mk)
    second = _propose(mk, proposal_id="prop.second", candidate_digest=DIGEST_B)
    third = mk.artifact_register_revision(
        "artifact.search-prompt", "r1", DIGEST_A, now=NOW,
    )

    assert [first["event_seq"], second["event_seq"], third["event_seq"]] == [1, 2, 3]
    assert [r["event_seq"] for r in _records(mk)] == [1, 2, 3]


# --------------------------------------------------------------------------
# Idempotency (§A-6, A1, A2)
# --------------------------------------------------------------------------

def test_slice1_exact_retry_with_the_same_operation_id_is_already_applied(tmp_path):
    mk = _mk(tmp_path)
    first = _propose(mk, operation_id="op-propose-1")
    before = len(_lines(mk))

    second = _propose(mk, operation_id="op-propose-1")

    assert second["outcome"] == "already_applied"
    assert second["record_id"] == first["record_id"]
    assert second["event_seq"] == first["event_seq"]
    assert second["record_fingerprint"] == first["record_fingerprint"]
    assert len(_lines(mk)) == before


def test_slice1_same_operation_id_with_a_changed_payload_is_a_mismatch(tmp_path):
    mk = _mk(tmp_path)
    _propose(mk, operation_id="op-propose-1")
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, operation_id="op-propose-1", summary="Lower the recency boost")

    assert excinfo.value.code == "E_IMPROVEMENT_IDEMPOTENCY_MISMATCH"
    assert excinfo.value.details["differing_keys"] == ["summary"]
    assert len(_lines(mk)) == before


def test_slice1_duplicate_proposal_id_is_rejected_and_writes_nothing(tmp_path):
    mk = _mk(tmp_path)
    _propose(mk)
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, summary="A different proposal reusing the id")

    assert excinfo.value.code == "E_IMPROVEMENT_ALREADY_EXISTS"
    assert len(_lines(mk)) == before


# --------------------------------------------------------------------------
# No invented authority (§4.1, A14)
# --------------------------------------------------------------------------

def test_slice1_human_authority_claim_is_still_unverified(tmp_path):
    mk = _mk(tmp_path)
    result = _propose(mk, authority_claim="human")
    assert result["record"]["authority_claim"] == "human"
    assert result["record"]["authority_verified"] is False
    assert _records(mk)[0]["authority_verified"] is False


def test_slice1_caller_supplied_authority_verified_true_is_a_hard_error(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, authority_verified=True)
    assert excinfo.value.code == "E_AUTHORITY_VERIFIED_FORBIDDEN"
    assert _lines(mk) == []


# --------------------------------------------------------------------------
# Scope fails closed (§4.1, A13)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("scope", ["profile", "shared"])
def test_slice1_wide_scope_without_a_host_authorization_ref_fails_closed(tmp_path, scope):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, scope=scope)
    assert excinfo.value.code == "E_IMPROVEMENT_SCOPE_UNAUTHORIZED"
    assert _lines(mk) == []


@pytest.mark.parametrize("scope", ["profile", "shared"])
def test_slice1_wide_scope_with_a_ref_is_stored_but_still_unverified(tmp_path, scope):
    """Core stores the breadcrumb; it does not claim to have checked it."""
    mk = _mk(tmp_path)
    result = _propose(mk, scope=scope, host_authorization_ref="host:grant-42")

    assert result["outcome"] == "applied"
    record = _records(mk)[0]
    assert record["scope"] == scope
    assert record["host_authorization_ref"] == "host:grant-42"
    assert record["authority_verified"] is False


@pytest.mark.parametrize("scope", ["session", "project"])
def test_slice1_narrow_scope_needs_no_host_authorization_ref(tmp_path, scope):
    mk = _mk(tmp_path)
    result = _propose(mk, scope=scope)
    assert result["outcome"] == "applied"
    assert result["record"]["host_authorization_ref"] is None


def test_slice1_unknown_scope_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, scope="custom")
    assert excinfo.value.code in ("E_IMPROVEMENT_VALIDATION", "E_IMPROVEMENT_PATTERN")
    assert _lines(mk) == []


# --------------------------------------------------------------------------
# Field bounds (§4)
# --------------------------------------------------------------------------

def test_slice1_summary_over_512_chars_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, summary="x" * 513)
    assert excinfo.value.code == "E_IMPROVEMENT_VALIDATION"
    assert _lines(mk) == []


def test_slice1_more_than_eight_evidence_refs_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, evidence_refs=["ref-%d" % i for i in range(9)])
    assert excinfo.value.code == "E_IMPROVEMENT_VALIDATION"
    assert _lines(mk) == []


def test_slice1_over_long_evidence_ref_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, evidence_refs=["r" * 257])
    assert excinfo.value.code == "E_IMPROVEMENT_VALIDATION"
    assert _lines(mk) == []


def test_slice1_non_sha256_candidate_digest_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, candidate_digest="not-a-digest")
    assert excinfo.value.code == "E_IMPROVEMENT_PATTERN"
    assert _lines(mk) == []


def test_slice1_malformed_proposal_id_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, proposal_id="Bad Id!")
    assert excinfo.value.code == "E_IMPROVEMENT_PATTERN"
    assert _lines(mk) == []


def test_slice1_empty_required_evaluations_is_rejected_at_declaration(tmp_path):
    """§5.1: P0 never permits an evidence-free promotion path."""
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, required_evaluations=[])
    assert excinfo.value.code == "E_IMPROVEMENT_VALIDATION"
    assert _lines(mk) == []


def test_slice1_more_than_eight_required_evaluations_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, required_evaluations=["kind-%d" % i for i in range(9)])
    assert excinfo.value.code == "E_IMPROVEMENT_VALIDATION"
    assert _lines(mk) == []


def test_slice1_duplicate_required_evaluation_kinds_are_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, required_evaluations=["suite-green", "suite-green"])
    assert excinfo.value.code == "E_IMPROVEMENT_VALIDATION"
    assert _lines(mk) == []


def test_slice1_malformed_execution_run_id_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, execution_run_id="NOT OK")
    assert excinfo.value.code == "E_IMPROVEMENT_PATTERN"
    assert _lines(mk) == []


# --------------------------------------------------------------------------
# Immutable artifact revisions (§5.5)
# --------------------------------------------------------------------------

def test_slice1_register_revision_appends_an_immutable_record(tmp_path):
    mk = _mk(tmp_path)
    _propose(mk)
    result = mk.artifact_register_revision(
        "artifact.search-prompt", "r1", DIGEST_A, now=NOW,
        locator="git:blob/abc", proposal_id=PROPOSAL["proposal_id"],
        provenance_refs=["prov-1"],
    )

    assert result["outcome"] == "applied"
    assert result["event_seq"] == 2

    record = _records(mk)[1]
    assert record["record_type"] == "artifact_revision"
    assert record["artifact_id"] == "artifact.search-prompt"
    assert record["revision_id"] == "r1"
    assert record["content_digest"] == DIGEST_A
    assert record["locator"] == "git:blob/abc"
    assert record["parent_revision_id"] is None
    assert record["proposal_id"] == PROPOSAL["proposal_id"]
    assert record["provenance_refs"] == ["prov-1"]
    assert record["authority_verified"] is False


def test_slice1_duplicate_revision_id_for_the_same_artifact_is_rejected(tmp_path):
    mk = _mk(tmp_path)
    mk.artifact_register_revision("artifact.search-prompt", "r1", DIGEST_A, now=NOW)
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        mk.artifact_register_revision("artifact.search-prompt", "r1", DIGEST_B, now=NOW)

    assert excinfo.value.code == "E_IMPROVEMENT_ALREADY_EXISTS"
    assert len(_lines(mk)) == before


def test_slice1_the_same_revision_id_under_a_different_artifact_is_allowed(tmp_path):
    mk = _mk(tmp_path)
    mk.artifact_register_revision("artifact.search-prompt", "r1", DIGEST_A, now=NOW)
    result = mk.artifact_register_revision("artifact.other", "r1", DIGEST_B, now=NOW)
    assert result["outcome"] == "applied"
    assert result["event_seq"] == 2


def test_slice1_missing_parent_revision_fails_closed(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        mk.artifact_register_revision(
            "artifact.search-prompt", "r2", DIGEST_B, now=NOW,
            parent_revision_id="r1",
        )
    assert excinfo.value.code == "E_IMPROVEMENT_NOT_FOUND"
    assert _lines(mk) == []


def test_slice1_parent_revision_of_another_artifact_does_not_count(tmp_path):
    mk = _mk(tmp_path)
    mk.artifact_register_revision("artifact.other", "r1", DIGEST_A, now=NOW)
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        mk.artifact_register_revision(
            "artifact.search-prompt", "r2", DIGEST_B, now=NOW,
            parent_revision_id="r1",
        )

    assert excinfo.value.code == "E_IMPROVEMENT_NOT_FOUND"
    assert len(_lines(mk)) == before


def test_slice1_missing_proposal_lineage_fails_closed(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        mk.artifact_register_revision(
            "artifact.search-prompt", "r1", DIGEST_A, now=NOW,
            proposal_id="prop.never-declared",
        )
    assert excinfo.value.code == "E_IMPROVEMENT_NOT_FOUND"
    assert _lines(mk) == []


def test_slice1_revision_rejects_a_non_sha256_content_digest(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        mk.artifact_register_revision("artifact.search-prompt", "r1", "nope", now=NOW)
    assert excinfo.value.code == "E_IMPROVEMENT_PATTERN"
    assert _lines(mk) == []


def test_slice1_revision_registration_is_idempotent(tmp_path):
    mk = _mk(tmp_path)
    first = mk.artifact_register_revision(
        "artifact.search-prompt", "r1", DIGEST_A, now=NOW, operation_id="op-rev-1",
    )
    before = len(_lines(mk))
    second = mk.artifact_register_revision(
        "artifact.search-prompt", "r1", DIGEST_A, now=NOW, operation_id="op-rev-1",
    )

    assert second["outcome"] == "already_applied"
    assert second["record_id"] == first["record_id"]
    assert len(_lines(mk)) == before


# --------------------------------------------------------------------------
# Projection of Slice 1 declarations (§6.2)
# --------------------------------------------------------------------------

def test_slice1_projection_of_an_untouched_base_dir_is_empty_and_creates_nothing(tmp_path):
    mk = _mk(tmp_path)
    view = mk.improvement_project(now=NOW)

    assert view["schema"] == 1
    assert view["high_water_seq"] == 0
    assert view["skipped_lines"] == 0
    assert view["proposals"] == {}
    assert view["artifacts"] == {}
    assert not _log_path(mk).exists()


def test_slice1_projection_reports_proposal_and_revision_declarations(tmp_path):
    mk = _mk(tmp_path)
    _propose(mk)
    mk.artifact_register_revision(
        "artifact.search-prompt", "r1", DIGEST_A, now=NOW,
        proposal_id=PROPOSAL["proposal_id"],
    )

    view = mk.improvement_project(now=NOW)

    assert view["generated_at"] == NOW
    assert view["high_water_seq"] == 2
    assert view["skipped_lines"] == 0

    proposal = view["proposals"][PROPOSAL["proposal_id"]]
    assert proposal["status"] == "draft"
    assert proposal["artifact_id"] == "artifact.search-prompt"
    assert proposal["candidate_digest"] == DIGEST_A
    assert proposal["required_evaluations"] == ["suite-green"]
    assert proposal["evaluations"] == {}
    assert proposal["status_history"] == []

    artifact = view["artifacts"]["artifact.search-prompt"]
    assert artifact["active_revision_id"] is None
    assert artifact["activations"] == []
    revision = artifact["revisions"]["r1"]
    assert revision["content_digest"] == DIGEST_A
    assert revision["parent_revision_id"] is None
    assert revision["proposal_id"] == PROPOSAL["proposal_id"]
    assert revision["event_seq"] == 2


def test_slice1_projection_is_deterministic_across_repeated_calls(tmp_path):
    mk = _mk(tmp_path)
    _propose(mk)
    _propose(mk, proposal_id="prop.second", candidate_digest=DIGEST_B)
    mk.artifact_register_revision("artifact.search-prompt", "r1", DIGEST_A, now=NOW)

    from memkraft.execution_protocol import digest

    first = mk.improvement_project(now=NOW)
    second = mk.improvement_project(now=NOW)
    assert digest(first) == digest(second)


def test_slice1_projection_filters_narrow_the_view(tmp_path):
    mk = _mk(tmp_path)
    _propose(mk)
    _propose(mk, proposal_id="prop.second", artifact_id="artifact.other",
             candidate_digest=DIGEST_B)
    mk.artifact_register_revision("artifact.other", "r1", DIGEST_B, now=NOW)

    by_artifact = mk.improvement_project(now=NOW, artifact_id="artifact.other")
    assert set(by_artifact["artifacts"]) == {"artifact.other"}
    assert set(by_artifact["proposals"]) == {"prop.second"}

    by_proposal = mk.improvement_project(now=NOW, proposal_id=PROPOSAL["proposal_id"])
    assert set(by_proposal["proposals"]) == {PROPOSAL["proposal_id"]}


# --------------------------------------------------------------------------
# Corrupt lines: readable, but write fail-closed (§6.3, A12)
# --------------------------------------------------------------------------

def test_slice1_corrupt_line_is_counted_by_the_projection_but_still_reads(tmp_path):
    mk = _mk(tmp_path)
    _propose(mk)
    with _log_path(mk).open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")

    view = mk.improvement_project(now=NOW)

    assert view["skipped_lines"] == 1
    assert view["high_water_seq"] == 1
    assert PROPOSAL["proposal_id"] in view["proposals"]


def test_slice1_writes_fail_closed_once_the_log_has_a_corrupt_line(tmp_path):
    mk = _mk(tmp_path)
    _propose(mk)
    with _log_path(mk).open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")
    before = len(_lines(mk))

    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, proposal_id="prop.second", candidate_digest=DIGEST_B)
    assert excinfo.value.code == "E_IMPROVEMENT_LOG_CORRUPT"

    with pytest.raises(ExecutionError) as excinfo:
        mk.artifact_register_revision("artifact.search-prompt", "r1", DIGEST_A, now=NOW)
    assert excinfo.value.code == "E_IMPROVEMENT_LOG_CORRUPT"

    assert len(_lines(mk)) == before


def test_slice1_a_parseable_line_missing_event_seq_counts_as_corrupt(tmp_path):
    """§6.3: structurally valid JSON is not a structurally valid record."""
    mk = _mk(tmp_path)
    _propose(mk)
    with _log_path(mk).open("a", encoding="utf-8") as handle:
        handle.write('{"record_type": "improvement_proposal", "id": "x"}\n')

    assert mk.improvement_project(now=NOW)["skipped_lines"] == 1
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, proposal_id="prop.second", candidate_digest=DIGEST_B)
    assert excinfo.value.code == "E_IMPROVEMENT_LOG_CORRUPT"


def test_slice1_a_parseable_line_missing_record_type_counts_as_corrupt(tmp_path):
    mk = _mk(tmp_path)
    _propose(mk)
    with _log_path(mk).open("a", encoding="utf-8") as handle:
        handle.write('{"event_seq": 2, "id": "x"}\n')

    assert mk.improvement_project(now=NOW)["skipped_lines"] == 1
    with pytest.raises(ExecutionError) as excinfo:
        _propose(mk, proposal_id="prop.second", candidate_digest=DIGEST_B)
    assert excinfo.value.code == "E_IMPROVEMENT_LOG_CORRUPT"
