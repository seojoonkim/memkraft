"""Slice 8 — typed handoff: declare, transition, export, import (plan §10, §4.6).

Handoff is the one place where MemKraft state leaves the base, so the rules are
deliberately narrow. Core never scrapes records to build an export: the payload
is supplied by the caller at ``handoff_declare``, stored verbatim, and exported
unchanged (D-25). There is nothing to filter at export time because core never
assembles an envelope out of private state.

Origin and copy are independent state machines (§10.5). The origin does not
learn that a recipient accepted; it reaches ``completed`` only by importing a
reverse envelope, which is an ordinary declare/export in the other direction.
That is why ``offered → accepted → completed`` is the whole machine and why a
second accept is a conflict rather than a silent no-op: re-firing a hook must be
safe (same ``operation_id``), while a genuinely competing accept must not be.

Expiry is projected, never stored. There is no ``expired`` record type and no
``expired`` field on any line — it is ``now > expires_at`` computed at read time,
which is what keeps a stalled handoff from needing a janitor.
"""
from __future__ import annotations

import pytest

from memkraft import MemKraft, store_core
from memkraft.execution_projection import project, project_handoffs
from memkraft.execution_protocol import ExecutionError, digest


NOW = "2026-08-04T11:22:33Z"
LATER = "2026-08-04T12:22:33Z"
EXPIRES = "2026-08-05T00:00:00Z"
PAST = "2026-08-03T00:00:00Z"
GOAL_ID = "hermes/release-3-3-0"
OTHER_GOAL_ID = "hermes/release-3-4-0"
ACTOR = "worker-3"

GOAL = dict(
    title="Release 3.3.0",
    intent="Ship the execution kernel",
    constraints=["stdlib only"],
    success_criteria=["suite green"],
)

PAYLOAD = {
    "summary": "v3 backup verified; staging suite still red on 2 billing tests.",
    "open_gates": ["staging-suite-green"],
    "next_intent": "Re-run the staging suite after the v4 view fix lands.",
}


def _mk(tmp_path, goal_ids=(GOAL_ID,)):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    for goal_id in goal_ids:
        mk.goal_declare(goal_id=goal_id, now=NOW, **GOAL)
    return mk


def _lines(mk):
    path = mk._execution_events_path()
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _records(mk):
    return store_core.read_all(mk._execution_events_path(),
                               include_tombstoned=True).records


def _declare(mk, goal_id=GOAL_ID, **kwargs):
    kwargs.setdefault("expires_at", EXPIRES)
    return mk.handoff_declare(goal_id, ACTOR, PAYLOAD, now=NOW, **kwargs)


# -- declaration ------------------------------------------------------------


def test_declare_stores_the_exact_typed_payload_and_its_digest(tmp_path):
    mk = _mk(tmp_path)

    result = _declare(mk)

    stored = _records(mk)[-1]
    assert stored["record_type"] == "handoff_declared"
    assert stored["payload"] == PAYLOAD           # verbatim, never rewritten
    assert stored["payload_digest"] == digest(PAYLOAD)
    assert stored["payload_schema"] == "memkraft.handoff.context/1"
    assert stored["to_actor"] == ACTOR
    assert stored["expires_at"] == EXPIRES
    assert stored["goal_id"] == GOAL_ID
    assert stored["emitted_at"] == NOW
    assert stored["execution_schema"] == 1
    assert stored["authority_verified"] is False
    assert result["outcome"] == "applied"
    assert result["handoff_id"] == stored["id"]
    assert result["payload_digest"] == digest(PAYLOAD)


def test_declared_handoff_is_tagged_public_safe_and_the_rest_stay_private(tmp_path):
    mk = _mk(tmp_path)

    handoff = _declare(mk)
    mk.handoff_transition(GOAL_ID, handoff["handoff_id"], "accepted", now=NOW)

    by_type = {row["record_type"]: row for row in _records(mk)}
    # §10.1: the caller decided this payload was shareable when they supplied it.
    assert by_type["handoff_declared"]["privacy"] == "public_safe"
    # A transition carries no payload, so it keeps the conservative default.
    assert by_type["handoff_transition"]["privacy"] == "local_private"


def test_binding_digest_is_stored_for_equality_and_pattern_checked(tmp_path):
    mk = _mk(tmp_path)
    binding = digest({"runtime": "hermes", "run_ref": "a1b2c3d4"})

    result = _declare(mk, binding_digest=binding)

    assert _records(mk)[-1]["binding_digest"] == binding
    assert result["outcome"] == "applied"

    with pytest.raises(ExecutionError) as excinfo:
        _declare(mk, binding_digest="not-a-digest")
    assert excinfo.value.code == "E_PATTERN"


def test_declare_rejects_a_payload_over_the_32_kib_cap(tmp_path):
    mk = _mk(tmp_path)
    oversized = {"summary": "x" * 40000}

    before = len(_lines(mk))
    with pytest.raises(ExecutionError) as excinfo:
        mk.handoff_declare(GOAL_ID, ACTOR, oversized, now=NOW, expires_at=EXPIRES)

    assert excinfo.value.code == "E_LIMIT_EXCEEDED"
    assert len(_lines(mk)) == before


def test_declare_rejects_a_payload_schema_outside_its_grammar(tmp_path):
    mk = _mk(tmp_path)

    with pytest.raises(ExecutionError) as excinfo:
        _declare(mk, payload_schema="Memkraft.Handoff/1")
    assert excinfo.value.code == "E_PATTERN"


def test_the_thirty_third_handoff_is_refused_and_appends_nothing(tmp_path):
    mk = _mk(tmp_path)
    for index in range(32):
        mk.handoff_declare(GOAL_ID, ACTOR, {"summary": "handoff %d" % index},
                           now=NOW, expires_at=EXPIRES)

    before = len(_lines(mk))
    with pytest.raises(ExecutionError) as excinfo:
        _declare(mk)

    assert excinfo.value.code == "E_LIMIT_EXCEEDED"
    assert len(_lines(mk)) == before


# -- transitions ------------------------------------------------------------


def test_offered_accepted_completed_is_the_whole_machine(tmp_path):
    mk = _mk(tmp_path)
    handoff_id = _declare(mk)["handoff_id"]

    assert project_handoffs(_records(mk), NOW, GOAL_ID)[handoff_id]["status"] \
        == "offered"
    accepted = mk.handoff_transition(GOAL_ID, handoff_id, "accepted", now=NOW)
    assert accepted["handoff_status"] == "accepted"
    completed = mk.handoff_transition(GOAL_ID, handoff_id, "completed", now=NOW)
    assert completed["handoff_status"] == "completed"
    assert project_handoffs(_records(mk), NOW, GOAL_ID)[handoff_id]["status"] \
        == "completed"


@pytest.mark.parametrize("path,to_state", [
    (["accepted"], "offered"),
    (["accepted", "completed"], "accepted"),
    (["accepted", "completed"], "offered"),
    ([], "completed"),
    ([], "offered"),
])
def test_forbidden_handoff_transitions_are_refused_with_no_append(tmp_path, path,
                                                                 to_state):
    mk = _mk(tmp_path)
    handoff_id = _declare(mk)["handoff_id"]
    for step in path:
        mk.handoff_transition(GOAL_ID, handoff_id, step, now=NOW)

    before = len(_lines(mk))
    with pytest.raises(ExecutionError) as excinfo:
        # A distinct operation_id, so this is a genuinely new attempt rather
        # than a replay: a replay is answered by §6.6 before any guard runs.
        mk.handoff_transition(GOAL_ID, handoff_id, to_state, now=NOW,
                              operation_id="9" * 64)

    assert excinfo.value.code in ("E_INVALID_TRANSITION", "E_CONFLICT")
    assert len(_lines(mk)) == before


def test_a_second_accept_conflicts_but_a_replayed_one_is_already_applied(tmp_path):
    mk = _mk(tmp_path)
    handoff_id = _declare(mk)["handoff_id"]
    operation_id = "b" * 64
    first = mk.handoff_transition(GOAL_ID, handoff_id, "accepted", now=NOW,
                                  operation_id=operation_id)

    # The same hook firing twice must be free; a genuinely competing accept
    # must not be (§10.5).
    replay = mk.handoff_transition(GOAL_ID, handoff_id, "accepted", now=NOW,
                                   operation_id=operation_id)
    assert replay["outcome"] == "already_applied"
    assert replay["record_id"] == first["record_id"]

    before = len(_lines(mk))
    with pytest.raises(ExecutionError) as excinfo:
        mk.handoff_transition(GOAL_ID, handoff_id, "accepted", now=NOW,
                              operation_id="c" * 64)
    assert excinfo.value.code == "E_CONFLICT"
    assert len(_lines(mk)) == before


def test_transition_against_a_guessed_handoff_id_is_not_declared(tmp_path):
    mk = _mk(tmp_path)
    _declare(mk)

    before = len(_lines(mk))
    with pytest.raises(ExecutionError) as excinfo:
        mk.handoff_transition(GOAL_ID, "f" * 32, "accepted", now=NOW)

    assert excinfo.value.code == "E_NOT_DECLARED"
    assert len(_lines(mk)) == before


def test_transition_requires_the_handoffs_own_goal(tmp_path):
    mk = _mk(tmp_path, goal_ids=(GOAL_ID, OTHER_GOAL_ID))
    handoff_id = _declare(mk)["handoff_id"]

    before = len(_lines(mk))
    with pytest.raises(ExecutionError) as excinfo:
        mk.handoff_transition(OTHER_GOAL_ID, handoff_id, "accepted", now=NOW)

    assert excinfo.value.code == "E_NOT_DECLARED"
    assert len(_lines(mk)) == before


# -- expiry -----------------------------------------------------------------


def test_expiry_is_projected_and_never_stored(tmp_path):
    mk = _mk(tmp_path)
    handoff_id = mk.handoff_declare(GOAL_ID, ACTOR, PAYLOAD, now=PAST,
                                    expires_at=NOW)["handoff_id"]

    assert all("expired" not in row for row in _records(mk))
    assert all(row["record_type"] != "handoff_expired" for row in _records(mk))
    assert project_handoffs(_records(mk), PAST, GOAL_ID)[handoff_id]["expired"] \
        is False
    assert project_handoffs(_records(mk), LATER, GOAL_ID)[handoff_id]["expired"] \
        is True


def test_an_expired_handoff_cannot_be_accepted(tmp_path):
    mk = _mk(tmp_path)
    handoff_id = mk.handoff_declare(GOAL_ID, ACTOR, PAYLOAD, now=PAST,
                                    expires_at=NOW)["handoff_id"]

    before = len(_lines(mk))
    with pytest.raises(ExecutionError) as excinfo:
        mk.handoff_transition(GOAL_ID, handoff_id, "accepted", now=LATER)

    assert excinfo.value.code == "E_HANDOFF_EXPIRED"
    assert len(_lines(mk)) == before


def test_a_completed_handoff_never_expires(tmp_path):
    mk = _mk(tmp_path)
    handoff_id = mk.handoff_declare(GOAL_ID, ACTOR, PAYLOAD, now=PAST,
                                    expires_at=NOW)["handoff_id"]
    mk.handoff_transition(GOAL_ID, handoff_id, "accepted", now=PAST)
    mk.handoff_transition(GOAL_ID, handoff_id, "completed", now=PAST)

    projected = project_handoffs(_records(mk), LATER, GOAL_ID)[handoff_id]
    assert projected["status"] == "completed"
    assert projected["expired"] is False


# -- export -----------------------------------------------------------------


def test_export_is_a_pure_read_and_reproduces_byte_identical_envelopes(tmp_path):
    mk = _mk(tmp_path)
    handoff_id = _declare(mk)["handoff_id"]

    before = len(_lines(mk))
    first = mk.handoff_export(GOAL_ID, handoff_id, now=NOW)["envelope"]
    second = mk.handoff_export(GOAL_ID, handoff_id, now=NOW)["envelope"]

    assert len(_lines(mk)) == before
    assert first == second
    assert first["envelope_schema"] == "memkraft.handoff/1"
    assert first["goal_id"] == GOAL_ID
    assert first["handoff_id"] == handoff_id
    assert first["payload"] == PAYLOAD
    assert first["payload_digest"] == digest(PAYLOAD)
    assert first["expires_at"] == EXPIRES
    assert first["exported_at"] == NOW
    assert first["envelope_digest"] == digest(
        {key: value for key, value in first.items() if key != "envelope_digest"}
    )


def test_export_of_an_undeclared_or_cross_goal_handoff_is_refused(tmp_path):
    mk = _mk(tmp_path, goal_ids=(GOAL_ID, OTHER_GOAL_ID))
    handoff_id = _declare(mk)["handoff_id"]

    for goal_id, target in ((GOAL_ID, "f" * 32), (OTHER_GOAL_ID, handoff_id)):
        with pytest.raises(ExecutionError) as excinfo:
            mk.handoff_export(goal_id, target, now=NOW)
        assert excinfo.value.code == "E_NOT_DECLARED"


# -- import -----------------------------------------------------------------


def _envelope(tmp_path, name="origin"):
    origin = _mk(tmp_path / name)
    handoff_id = _declare(origin)["handoff_id"]
    return origin, handoff_id, origin.handoff_export(GOAL_ID, handoff_id,
                                                     now=NOW)["envelope"]


def test_import_allocates_a_local_id_and_records_the_origin_triple(tmp_path):
    _origin, handoff_id, envelope = _envelope(tmp_path)
    recipient = _mk(tmp_path / "recipient")

    result = recipient.handoff_import(GOAL_ID, envelope, now=NOW)

    stored = _records(recipient)[-1]
    assert result["outcome"] == "applied"
    assert stored["record_type"] == "handoff_imported"
    assert result["handoff_id"] == stored["id"]
    assert result["handoff_id"] != handoff_id       # a local identity, not theirs
    assert stored["imported_from"] == {
        "origin_instance_id": envelope["origin_instance_id"],
        "handoff_id": handoff_id,
        "payload_digest": envelope["payload_digest"],
    }
    assert stored["payload"] == PAYLOAD
    assert project_handoffs(_records(recipient), NOW, GOAL_ID)[
        result["handoff_id"]]["status"] == "offered"


def test_an_imported_handoff_walks_its_own_state_machine(tmp_path):
    _origin, _handoff_id, envelope = _envelope(tmp_path)
    recipient = _mk(tmp_path / "recipient")
    local_id = recipient.handoff_import(GOAL_ID, envelope, now=NOW)["handoff_id"]

    recipient.handoff_transition(GOAL_ID, local_id, "accepted", now=NOW)
    recipient.handoff_transition(GOAL_ID, local_id, "completed", now=NOW)

    assert project_handoffs(_records(recipient), NOW, GOAL_ID)[local_id]["status"] \
        == "completed"


def test_reimport_is_already_applied_and_appends_nothing(tmp_path):
    _origin, _handoff_id, envelope = _envelope(tmp_path)
    recipient = _mk(tmp_path / "recipient")
    first = recipient.handoff_import(GOAL_ID, envelope, now=NOW,
                                     operation_id="d" * 64)

    before = len(_lines(recipient))
    # A different operation_id: the dedupe here is the origin triple, not the
    # idempotency key, because an ack lost in transit is retried by a new caller.
    replay = recipient.handoff_import(GOAL_ID, envelope, now=NOW,
                                      operation_id="e" * 64)

    assert replay["outcome"] == "already_applied"
    assert replay["handoff_id"] == first["handoff_id"]
    assert len(_lines(recipient)) == before


def test_same_origin_handoff_with_a_different_payload_conflicts(tmp_path):
    _origin, _handoff_id, envelope = _envelope(tmp_path)
    recipient = _mk(tmp_path / "recipient")
    recipient.handoff_import(GOAL_ID, envelope, now=NOW)

    forked = dict(envelope, payload={"summary": "a different story"})
    forked["payload_digest"] = digest(forked["payload"])
    forked["envelope_digest"] = digest(
        {key: value for key, value in forked.items() if key != "envelope_digest"}
    )

    before = len(_lines(recipient))
    with pytest.raises(ExecutionError) as excinfo:
        recipient.handoff_import(GOAL_ID, forked, now=NOW)

    assert excinfo.value.code == "E_CONFLICT"
    assert len(_lines(recipient)) == before


def test_a_tampered_payload_fails_the_digest_check(tmp_path):
    _origin, _handoff_id, envelope = _envelope(tmp_path)
    recipient = _mk(tmp_path / "recipient")
    tampered = dict(envelope, payload={"summary": "tampered"})

    before = len(_lines(recipient))
    with pytest.raises(ExecutionError) as excinfo:
        recipient.handoff_import(GOAL_ID, tampered, now=NOW)

    assert excinfo.value.code == "E_DIGEST_MISMATCH"
    assert len(_lines(recipient)) == before


def test_a_tampered_envelope_field_fails_the_envelope_digest_check(tmp_path):
    _origin, _handoff_id, envelope = _envelope(tmp_path)
    recipient = _mk(tmp_path / "recipient")
    tampered = dict(envelope, expires_at=LATER)

    with pytest.raises(ExecutionError) as excinfo:
        recipient.handoff_import(GOAL_ID, tampered, now=NOW)
    assert excinfo.value.code == "E_DIGEST_MISMATCH"


def test_an_expired_envelope_is_refused(tmp_path):
    origin = _mk(tmp_path / "origin")
    handoff_id = origin.handoff_declare(GOAL_ID, ACTOR, PAYLOAD, now=PAST,
                                        expires_at=NOW)["handoff_id"]
    envelope = origin.handoff_export(GOAL_ID, handoff_id, now=PAST)["envelope"]
    recipient = _mk(tmp_path / "recipient")

    before = len(_lines(recipient))
    with pytest.raises(ExecutionError) as excinfo:
        recipient.handoff_import(GOAL_ID, envelope, now=LATER)

    assert excinfo.value.code == "E_HANDOFF_EXPIRED"
    assert len(_lines(recipient)) == before


def test_the_envelope_key_set_is_closed(tmp_path):
    _origin, _handoff_id, envelope = _envelope(tmp_path)
    recipient = _mk(tmp_path / "recipient")

    with pytest.raises(ExecutionError) as excinfo:
        recipient.handoff_import(GOAL_ID, dict(envelope, extra="anything"),
                                 now=NOW)
    assert excinfo.value.code == "E_UNKNOWN_FIELD"

    incomplete = {key: value for key, value in envelope.items()
                  if key != "payload_schema"}
    with pytest.raises(ExecutionError) as excinfo:
        recipient.handoff_import(GOAL_ID, incomplete, now=NOW)
    assert excinfo.value.code == "E_MISSING_FIELD"


def test_import_takes_no_path_parameter(tmp_path):
    import inspect

    parameters = inspect.signature(MemKraft.handoff_import).parameters
    assert "envelope" in parameters
    assert not any("path" in name or "dir" in name or "url" in name
                   for name in parameters)


# -- fencing, assessment, and deletion --------------------------------------


def test_declare_is_fence_protected_under_the_handoff_scope(tmp_path):
    mk = _mk(tmp_path)
    lease = mk.lease_acquire(GOAL_ID, "handoff", "worker-1", 600, now=NOW)

    before = len(_lines(mk))
    with pytest.raises(ExecutionError) as excinfo:
        _declare(mk)
    assert excinfo.value.code == "E_FENCE_REQUIRED"
    assert len(_lines(mk)) == before

    assert _declare(mk, fence_token=lease["fence_token"])["outcome"] == "applied"


def test_a_fence_token_on_an_unleased_handoff_scope_is_refused(tmp_path):
    mk = _mk(tmp_path)

    with pytest.raises(ExecutionError) as excinfo:
        _declare(mk, fence_token=1)
    assert excinfo.value.code == "E_UNKNOWN_FIELD"


def test_an_accepted_handoff_makes_the_assessment_ask_for_repair(tmp_path):
    mk = _mk(tmp_path)
    handoff_id = _declare(mk)["handoff_id"]
    mk.handoff_transition(GOAL_ID, handoff_id, "accepted", now=NOW)

    assessment = mk.assess_run(GOAL_ID, now=NOW)

    assert assessment["recommendation"] == "repair"
    assert assessment["reason_code"] == "handoff_incomplete"


def test_forgetting_a_goal_removes_its_handoffs_from_export(tmp_path):
    mk = _mk(tmp_path)
    handoff_id = _declare(mk)["handoff_id"]

    mk.forget({"goal_id": GOAL_ID}, dry_run=False)

    assert project(store_core.read_all(mk._execution_events_path()).records,
                   NOW, GOAL_ID)["goal_status"] is None
    with pytest.raises(ExecutionError) as excinfo:
        mk.handoff_export(GOAL_ID, handoff_id, now=NOW)
    assert excinfo.value.code == "E_NOT_DECLARED"


def test_the_projection_is_deterministic_over_handoffs(tmp_path):
    mk = _mk(tmp_path)
    handoff_id = _declare(mk)["handoff_id"]
    mk.handoff_transition(GOAL_ID, handoff_id, "accepted", now=NOW)
    records = _records(mk)

    digests = {project(records, NOW, GOAL_ID)["digest"] for _ in range(50)}
    shuffled = project(list(reversed(records)), NOW, GOAL_ID)

    assert len(digests) == 1
    assert shuffled["digest"] == digests.pop()
    assert shuffled["consistent"] is True
