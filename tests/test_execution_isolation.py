"""Slice 8 — handoff isolation, the redaction lint, and privacy (plan §10.1–§10.4).

``IS-01``. Two claims are tested here and they are different claims.

*Isolation* is structural: ``handoff_import`` takes an envelope object and
nothing else. There is no path, base_dir, profile, or URL parameter anywhere in
the operation, so a cross-base read is inexpressible rather than merely
discouraged. The proofs are mechanical — delete the origin base entirely and the
import still succeeds, and run the import with ``open`` monkeypatched and assert
zero opens outside the target base.

*Redaction* is a lint, not a security control. It catches obvious mistakes —
absolute paths, common secret prefixes — and refuses the export when it fires.
It does not detect novel secret formats and it must not be relied upon to make
an untrusted payload safe. Deciding what is safe to share is the caller's job,
discharged at ``handoff_declare`` time. It fails closed: a hit refuses the
export rather than substituting ``[redacted]``, because an envelope that looks
complete with semantics quietly removed teaches the exporter nothing.
"""
from __future__ import annotations

import builtins
import getpass
import json
import os
import shutil

import pytest

from memkraft import MemKraft, store_core
from memkraft.execution_projection import project_handoffs
from memkraft.execution_protocol import ExecutionError


NOW = "2026-08-04T11:22:33Z"
EXPIRES = "2026-08-05T00:00:00Z"
GOAL_ID = "hermes/release-3-3-0"
OTHER_GOAL_ID = "hermes/release-3-4-0"
ACTOR = "worker-3"

GOAL = dict(
    title="Release 3.3.0",
    intent="Ship the execution kernel",
    constraints=["stdlib only"],
    success_criteria=["suite green"],
)

PAYLOAD = {"summary": "staging suite still red on 2 billing tests."}


def _mk(base_dir, goal_ids=(GOAL_ID,)):
    mk = MemKraft(base_dir=str(base_dir))
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


def _export(mk, payload=PAYLOAD, goal_id=GOAL_ID):
    handoff_id = mk.handoff_declare(goal_id, ACTOR, payload, now=NOW,
                                    expires_at=EXPIRES)["handoff_id"]
    return handoff_id, mk.handoff_export(goal_id, handoff_id, now=NOW)["envelope"]


# -- IS-01: no cross-base read ----------------------------------------------


def test_import_succeeds_after_the_origin_base_is_deleted(tmp_path):
    origin = _mk(tmp_path / "origin")
    _handoff_id, envelope = _export(origin)
    # The envelope bytes are all that crosses. Everything else is unreachable
    # by construction, and this proves it rather than asserting it.
    wire = json.loads(json.dumps(envelope))
    shutil.rmtree(str(tmp_path / "origin"))

    recipient = _mk(tmp_path / "recipient")
    result = recipient.handoff_import(GOAL_ID, wire, now=NOW)

    assert result["outcome"] == "applied"
    assert project_handoffs(_records(recipient), NOW, GOAL_ID)[
        result["handoff_id"]]["status"] == "offered"


def test_import_opens_nothing_outside_the_target_base(tmp_path, monkeypatch):
    origin = _mk(tmp_path / "origin")
    _handoff_id, envelope = _export(origin)
    recipient = _mk(tmp_path / "recipient")

    base = os.path.realpath(str(tmp_path / "recipient"))
    opened = []
    real_open = builtins.open

    def recording_open(file, *args, **kwargs):
        opened.append(os.path.realpath(str(file)))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", recording_open)
    try:
        recipient.handoff_import(GOAL_ID, envelope, now=NOW)
    finally:
        monkeypatch.setattr(builtins, "open", real_open)

    assert opened
    assert [path for path in opened if not path.startswith(base + os.sep)] == []


def test_the_envelope_carries_no_path_or_account_strings(tmp_path):
    origin = _mk(tmp_path / "origin")
    _handoff_id, envelope = _export(origin)

    wire = json.dumps(envelope)
    for leak in (str(tmp_path), os.path.expanduser("~"), getpass.getuser(),
                 os.sep + "Users" + os.sep, "/home/", "/tmp/"):
        if leak:
            assert leak not in wire
    assert envelope["origin_instance_id"] != str(tmp_path / "origin")


def test_origin_instance_id_is_lazy_random_and_stable(tmp_path):
    origin = _mk(tmp_path / "origin")
    marker = tmp_path / "origin" / ".memkraft" / "origin_instance_id"
    assert not marker.exists()          # nothing until the first export

    _handoff_id, first = _export(origin)
    _handoff_id, second = _export(origin)
    other = _mk(tmp_path / "other")
    _other_id, elsewhere = _export(other)

    assert marker.exists()
    assert first["origin_instance_id"] == second["origin_instance_id"]
    assert len(first["origin_instance_id"]) == 32
    assert int(first["origin_instance_id"], 16) >= 0
    assert first["origin_instance_id"] != elsewhere["origin_instance_id"]


# -- the redaction lint, fail-closed ----------------------------------------


@pytest.mark.parametrize("planted", [
    "/Users/someone/secrets.txt",
    "see /home/agent/keys for the token",
    "C:\\Users\\agent\\key.pem",
    "sk-ABCDEFGHIJKLMNOPQRSTUVWX",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "AKIAABCDEFGHIJKLMNOP",
    "-----BEGIN RSA PRIVATE KEY-----",
    "xoxb-ABCDEFGHIJKL",
])
def test_export_fails_closed_on_a_planted_pattern(tmp_path, planted):
    mk = _mk(tmp_path)
    handoff_id = mk.handoff_declare(GOAL_ID, ACTOR, {"summary": planted},
                                    now=NOW, expires_at=EXPIRES)["handoff_id"]

    with pytest.raises(ExecutionError) as excinfo:
        mk.handoff_export(GOAL_ID, handoff_id, now=NOW)

    error = excinfo.value
    assert error.code == "E_PATTERN"
    # The rule name is actionable; the matched text is the thing we refuse to
    # copy into a log the runtime may ship elsewhere (§6.4).
    assert error.details.get("rule")
    assert planted not in str(error)
    assert planted not in json.dumps(error.details)


def test_export_fails_closed_on_the_base_dir_and_the_username(tmp_path):
    mk = _mk(tmp_path)
    for leak in (str(tmp_path), getpass.getuser()):
        handoff_id = mk.handoff_declare(
            GOAL_ID, ACTOR, {"summary": "artifact at %s" % leak},
            now=NOW, expires_at=EXPIRES)["handoff_id"]
        with pytest.raises(ExecutionError) as excinfo:
            mk.handoff_export(GOAL_ID, handoff_id, now=NOW)
        assert excinfo.value.code == "E_PATTERN"


def test_a_refused_export_neither_redacts_nor_writes(tmp_path):
    mk = _mk(tmp_path)
    payload = {"summary": "/Users/someone/secrets.txt"}
    handoff_id = mk.handoff_declare(GOAL_ID, ACTOR, payload, now=NOW,
                                    expires_at=EXPIRES)["handoff_id"]

    before = len(_lines(mk))
    with pytest.raises(ExecutionError):
        mk.handoff_export(GOAL_ID, handoff_id, now=NOW)

    stored = [row for row in _records(mk)
              if row.get("record_type") == "handoff_declared"][0]
    assert stored["payload"] == payload         # no [redacted] substitution
    assert len(_lines(mk)) == before


def test_a_clean_payload_still_exports(tmp_path):
    mk = _mk(tmp_path)
    _handoff_id, envelope = _export(mk, {"summary": "no secrets here",
                                         "open_gates": ["staging-suite-green"]})
    assert envelope["payload"]["open_gates"] == ["staging-suite-green"]


# -- source / recipient isolation -------------------------------------------


def test_the_recipient_accepting_leaves_the_origin_offered(tmp_path):
    origin = _mk(tmp_path / "origin")
    origin_handoff_id, envelope = _export(origin)
    recipient = _mk(tmp_path / "recipient")

    local_id = recipient.handoff_import(GOAL_ID, envelope, now=NOW)["handoff_id"]
    recipient.handoff_transition(GOAL_ID, local_id, "accepted", now=NOW)

    # Origin and copy are independent state machines (§10.5). The origin only
    # advances when a reverse envelope is imported back into it.
    assert project_handoffs(_records(origin), NOW, GOAL_ID)[
        origin_handoff_id]["status"] == "offered"
    assert project_handoffs(_records(recipient), NOW, GOAL_ID)[
        local_id]["status"] == "accepted"


def test_an_import_lands_only_in_the_named_goal(tmp_path):
    origin = _mk(tmp_path / "origin")
    _handoff_id, envelope = _export(origin)
    recipient = _mk(tmp_path / "recipient", goal_ids=(GOAL_ID, OTHER_GOAL_ID))

    local_id = recipient.handoff_import(OTHER_GOAL_ID, envelope,
                                        now=NOW)["handoff_id"]

    assert project_handoffs(_records(recipient), NOW, GOAL_ID) == {}
    assert local_id in project_handoffs(_records(recipient), NOW, OTHER_GOAL_ID)


def test_import_into_an_undeclared_goal_is_refused(tmp_path):
    origin = _mk(tmp_path / "origin")
    _handoff_id, envelope = _export(origin)
    recipient = MemKraft(base_dir=str(tmp_path / "recipient"))
    recipient.init(verbose=False)

    before = len(_lines(recipient))
    with pytest.raises(ExecutionError) as excinfo:
        recipient.handoff_import(GOAL_ID, envelope, now=NOW)

    assert excinfo.value.code == "E_NOT_DECLARED"
    assert len(_lines(recipient)) == before


def test_no_error_detail_leaks_payload_or_actor_values(tmp_path):
    origin = _mk(tmp_path / "origin")
    _handoff_id, envelope = _export(origin, {"summary": "confidential-marker"})
    recipient = _mk(tmp_path / "recipient")
    recipient.handoff_import(GOAL_ID, envelope, now=NOW)

    forked = dict(envelope, payload={"summary": "second-confidential-marker"})

    with pytest.raises(ExecutionError) as excinfo:
        recipient.handoff_import(GOAL_ID, forked, now=NOW)

    rendered = str(excinfo.value) + json.dumps(excinfo.value.details)
    assert "confidential-marker" not in rendered
    assert ACTOR not in rendered
