"""Wished-for tests for MemKraft 3.8 Feature 2: decision authority provenance.

An authority assertion is an inert, append-only *pointer* to where authority
actually lives. Recording one is not verifying one: MemKraft never checks the
external system, never approves, dispatches, schedules, or edits skills, and
never lets a claim become a permission. Hermes owns all of that.
"""
from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timedelta, timezone

import pytest

from memkraft import MemKraft
from memkraft import authority as authority_module
from memkraft.authority import (
    MAX_ACTIVE_AUTHORITY,
    MAX_AUTHORITY_SCOPES,
    MAX_AUTHORITY_TOKENS,
    AuthorityError,
)


NOW = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 8, 18, 9, 0, 0)

SOURCE_REF = "hermes://policy/release-cut"
VERSION = "policy-2026.08.1"
SCOPES = ["release"]


def _later(seconds):
    return NOW + timedelta(seconds=seconds)


def _assert(mk, authority_id="release-cut", **overrides):
    kwargs = {
        "source_ref": SOURCE_REF,
        "authority_version": VERSION,
        "scopes": list(SCOPES),
        "issued_at": NOW,
    }
    kwargs.update(overrides)
    return mk.authority_assert(authority_id, **kwargs)


def _seed(mk):
    """A little canonical/timeline content so the compiler has real sections."""
    mk.append_event("release", "owner", "Sano", source="runbook.md")
    mk.append_event("staging", "cluster", "seoul-a", source="runbook.md")
    mk.compile_truth(dry_run=False)


# --------------------------------------------------------------------------
# module hygiene
# --------------------------------------------------------------------------

def test_authority_annotations_are_python39_compatible():
    tree = ast.parse(inspect.getsource(authority_module))
    annotations = [
        node.annotation
        for node in ast.walk(tree)
        if isinstance(node, (ast.arg, ast.AnnAssign)) and node.annotation is not None
    ] + [
        node.returns
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None
    ]
    assert not any(
        isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
        for annotation in annotations
        for node in ast.walk(annotation)
    )


def test_authority_module_adds_no_execution_or_approval_surface():
    source = inspect.getsource(authority_module)
    for forbidden in ("approve", "dispatch", "schedule", "gate_", "skill", "subprocess",
                      "permission", "verify"):
        assert forbidden not in source


def test_authority_mixin_exposes_only_the_four_authority_methods():
    public = {
        name for name in vars(authority_module.AuthorityMixin)
        if not name.startswith("_")
    }
    assert public == {
        "authority_assert", "authority_revoke", "authority_active", "authority_states",
    }


# --------------------------------------------------------------------------
# asserting
# --------------------------------------------------------------------------

def test_authority_assert_requires_timezone_aware_issued_at(tmp_path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(AuthorityError) as excinfo:
        _assert(mk, issued_at=NAIVE)
    assert excinfo.value.code == "E_AUTHORITY_VALIDATION"


def test_authority_assert_requires_issued_at_to_be_a_datetime(tmp_path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(AuthorityError):
        _assert(mk, issued_at="2026-08-18T09:00:00Z")


@pytest.mark.parametrize("authority_id", ["", "   ", "a" * 81, 7, None, "bad id!"])
def test_authority_assert_rejects_invalid_authority_id(tmp_path, authority_id):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(AuthorityError):
        _assert(mk, authority_id)


@pytest.mark.parametrize("source_ref", ["", "   ", "a" * 513, 7, None])
def test_authority_assert_requires_a_source_ref(tmp_path, source_ref):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(AuthorityError):
        _assert(mk, source_ref=source_ref)


@pytest.mark.parametrize("version", ["", "   ", "a" * 81, 7, None, "bad version!"])
def test_authority_assert_requires_an_authority_version(tmp_path, version):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(AuthorityError):
        _assert(mk, authority_version=version)


@pytest.mark.parametrize("scopes", [
    None, [], "release", ["", "x"], ["bad scope!"], [7], ["a" * 81],
    ["s%d" % index for index in range(MAX_AUTHORITY_SCOPES + 1)],
])
def test_authority_assert_requires_bounded_scopes(tmp_path, scopes):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(AuthorityError):
        _assert(mk, scopes=scopes)


@pytest.mark.parametrize("expires_at", [NAIVE, "2026-08-18T10:00:00Z", 7, NOW,
                                        NOW - timedelta(seconds=1)])
def test_authority_assert_rejects_an_unusable_expires_at(tmp_path, expires_at):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(AuthorityError):
        _assert(mk, expires_at=expires_at)


def test_authority_assert_writes_an_append_only_inert_record(tmp_path):
    mk = MemKraft(str(tmp_path))
    result = _assert(mk, scopes=["release", "release", "deploy"],
                     expires_at=_later(600))
    assert result["outcome"] == "applied"
    record = result["record"]
    assert record["record_type"] == "authority_assertion"
    assert record["authority_id"] == "release-cut"
    assert record["source_ref"] == SOURCE_REF
    assert record["authority_version"] == VERSION
    # deduplicated and sorted so the same claim renders identically every time
    assert record["scopes"] == ["deploy", "release"]
    assert record["issued_at"] == "2026-08-18T09:00:00Z"
    assert record["expires_at"] == "2026-08-18T09:10:00Z"
    assert record["status"] == "asserted"
    assert record["privacy"] == "local_private"
    assert record["authority_verified"] is False

    path = mk._authority_events_path()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    _assert(mk, issued_at=_later(60))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_authority_assert_never_claims_verification(tmp_path):
    mk = MemKraft(str(tmp_path))
    result = _assert(mk)
    assert result["record"]["authority_verified"] is False
    assert mk.authority_active(now=NOW)[0]["authority_verified"] is False


# --------------------------------------------------------------------------
# lifecycle status
# --------------------------------------------------------------------------

def test_authority_revoke_supersedes_without_rewriting_history(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk)
    mk.authority_revoke("release-cut", issued_at=_later(60))
    assert mk.authority_active(now=_later(120)) == []
    states = mk.authority_states(now=_later(120))
    assert [(row["authority_id"], row["status"]) for row in states] == [
        ("release-cut", "revoked")
    ]
    assert len(mk._authority_events_path().read_text(encoding="utf-8").splitlines()) == 2


def test_authority_revoke_requires_aware_issued_at(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk)
    with pytest.raises(AuthorityError):
        mk.authority_revoke("release-cut", issued_at=NAIVE)


def test_later_assertion_supersedes_the_earlier_one(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk, authority_version="policy-2026.08.1")
    _assert(mk, authority_version="policy-2026.08.2", issued_at=_later(60))
    active = mk.authority_active(now=_later(120))
    assert [row["authority_version"] for row in active] == ["policy-2026.08.2"]
    states = mk.authority_states(now=_later(120))
    assert [row["status"] for row in states] == ["asserted", "superseded"]
    assert [row["authority_version"] for row in states] == [
        "policy-2026.08.2", "policy-2026.08.1",
    ]


def test_assertion_after_revocation_is_asserted_again(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk)
    mk.authority_revoke("release-cut", issued_at=_later(60))
    _assert(mk, issued_at=_later(120))
    assert [row["status"] for row in mk.authority_states(now=_later(180))][0] == "asserted"
    assert len(mk.authority_active(now=_later(180))) == 1


def test_future_authority_events_do_not_affect_an_earlier_as_of_now(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk, authority_version="policy-2026.08.1")
    before = mk.authority_active(now=_later(30))
    mk.authority_revoke("release-cut", issued_at=_later(120))
    _assert(mk, authority_version="policy-2026.08.2", issued_at=_later(180))
    assert mk.authority_active(now=_later(30)) == before
    assert [(row["authority_version"], row["status"])
            for row in mk.authority_states(now=_later(30))] == [
        ("policy-2026.08.1", "asserted")
    ]
    assert [row["authority_version"] for row in mk.authority_active(now=_later(240))] == [
        "policy-2026.08.2"
    ]


def test_same_timestamp_assertions_resolve_by_append_order(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk, authority_version="policy-2026.08.1")
    _assert(mk, authority_version="policy-2026.08.2")
    active = mk.authority_active(now=_later(1))
    assert [row["authority_version"] for row in active] == ["policy-2026.08.2"]
    states = mk.authority_states(now=_later(1))
    assert [(row["authority_version"], row["status"]) for row in states] == [
        ("policy-2026.08.1", "superseded"),
        ("policy-2026.08.2", "asserted"),
    ]


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

def test_authority_active_requires_aware_now(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk)
    with pytest.raises(AuthorityError):
        mk.authority_active(now=NAIVE)
    with pytest.raises(AuthorityError):
        mk.authority_states(now=NAIVE)


def test_authority_active_excludes_expired_and_future_assertions(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk, expires_at=_later(600))
    assert len(mk.authority_active(now=_later(599))) == 1
    assert mk.authority_active(now=_later(600)) == []
    assert mk.authority_active(now=NOW - timedelta(seconds=1)) == []


def test_authority_without_expiry_stays_active(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk)
    active = mk.authority_active(now=_later(86400 * 30))
    assert len(active) == 1
    assert active[0].get("expires_at") is None


def test_authority_active_is_deterministic_and_capped(tmp_path):
    mk = MemKraft(str(tmp_path))
    for index in range(MAX_ACTIVE_AUTHORITY + 2):
        _assert(mk, "authority-%d" % index, issued_at=_later(index))
    active = mk.authority_active(now=_later(100))
    assert len(active) == MAX_ACTIVE_AUTHORITY
    # newest first, then authority_id
    assert [row["authority_id"] for row in active] == [
        "authority-4", "authority-3", "authority-2",
    ]
    assert active == mk.authority_active(now=_later(100))


def test_authority_active_rejects_an_out_of_range_limit(tmp_path):
    mk = MemKraft(str(tmp_path))
    for limit in (0, -1, True, 1.5, "2", None, MAX_ACTIVE_AUTHORITY + 1):
        with pytest.raises(AuthorityError):
            mk.authority_active(now=NOW, limit=limit)


def test_authority_active_skips_malformed_persisted_records(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk)
    base = {
        "schema_version": 1, "record_type": "authority_assertion",
        "source_ref": SOURCE_REF, "authority_version": VERSION,
        "scopes": ["release"], "issued_at": "2026-08-18T09:00:00Z",
        "expires_at": None, "status": "asserted",
        "privacy": "local_private", "authority_verified": False,
    }
    with open(mk._authority_events_path(), "a", encoding="utf-8") as handle:
        for index, bad in enumerate((
            {"authority_id": "bad id!"},
            {"authority_id": "unverified", "authority_verified": True},
            {"authority_id": "no-source", "source_ref": ""},
            {"authority_id": "no-scopes", "scopes": []},
            {"authority_id": "wrong-scopes", "scopes": "release"},
            {"authority_id": "duplicate-scopes", "scopes": ["release", "release"]},
            {"authority_id": "unsorted-scopes", "scopes": ["write", "release"]},
            {"authority_id": "padded-source", "source_ref": " hermes://policy/release-cut "},
            {"authority_id": "control-source", "source_ref": "hermes://policy/release\ncut"},
            {"authority_id": "no-version", "authority_version": None},
            {"authority_id": "inverted", "expires_at": "2026-08-18T08:00:00Z"},
            {"authority_id": "bad-time", "issued_at": "yesterday"},
            {"authority_id": "wrong-status", "status": "approved"},
            {"authority_id": "wrong-privacy", "privacy": "shared"},
            {"authority_id": "wrong-schema", "schema_version": 2},
        )):
            record = dict(base, id="x%d" % index)
            record.update(bad)
            handle.write(json.dumps(record) + "\n")
    assert [row["authority_id"] for row in mk.authority_active(now=_later(60))] == [
        "release-cut"
    ]


# --------------------------------------------------------------------------
# compiler integration
# --------------------------------------------------------------------------

def test_compile_context_is_byte_identical_when_authority_budget_is_zero(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    baseline = mk.compile_context("answer the user", 800, now=NOW)

    _assert(mk)
    default = mk.compile_context("answer the user", 800, now=NOW)
    explicit = mk.compile_context("answer the user", 800, now=NOW, authority_budget=0)

    for candidate in (default, explicit):
        assert candidate["usage_id"] == baseline["usage_id"]
        assert json.dumps(candidate, sort_keys=True) == json.dumps(baseline, sort_keys=True)
        assert "authority" not in candidate["sections"]


@pytest.mark.parametrize("authority_budget", [-1, True, 1.5, "10", None])
def test_compile_context_rejects_invalid_authority_budget(tmp_path, authority_budget):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(ValueError, match="authority_budget"):
        mk.compile_context("answer the user", 800, now=NOW,
                           authority_budget=authority_budget)


def test_compile_context_rejects_authority_budget_that_does_not_fit(tmp_path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(ValueError, match="authority_budget"):
        mk.compile_context("answer the user", 100, now=NOW, authority_budget=100)
    with pytest.raises(ValueError, match="authority_budget"):
        mk.compile_context("answer the user", 400, now=NOW,
                           focus_budget=200, authority_budget=200)


def test_compile_context_requires_aware_now_for_authority(tmp_path):
    mk = MemKraft(str(tmp_path))
    with pytest.raises(ValueError, match="now"):
        mk.compile_context("answer the user", 800, authority_budget=200)
    with pytest.raises(ValueError, match="now"):
        mk.compile_context("answer the user", 800, now=NAIVE, authority_budget=200)


def test_compile_context_includes_active_authority_when_opted_in(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    _assert(mk)
    result = mk.compile_context("answer the user", 800, now=NOW, authority_budget=300)
    section = result["sections"]["authority"]
    assert [item["authority_id"] for item in section] == ["release-cut"]
    assert section[0]["source"] == "decision_authority"
    assert section[0]["source_ref"] == SOURCE_REF
    assert section[0]["authority_version"] == VERSION
    assert section[0]["scopes"] == ["release"]
    assert section[0]["status"] == "asserted"
    assert section[0]["authority_verified"] is False
    assert result["estimated_tokens"] <= 800
    assert result == mk.compile_context("answer the user", 800, now=NOW,
                                        authority_budget=300)


def test_compile_context_omits_revoked_superseded_and_expired_authority(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    _assert(mk, "revoked-one")
    mk.authority_revoke("revoked-one", issued_at=_later(30))
    _assert(mk, "expired-one", expires_at=_later(60))
    _assert(mk, "superseded-one", authority_version="policy-2026.08.1")
    _assert(mk, "superseded-one", authority_version="policy-2026.08.2",
            issued_at=_later(30))
    result = mk.compile_context("answer the user", 2000, now=_later(120),
                                authority_budget=300)
    section = result["sections"].get("authority", [])
    assert [item["authority_id"] for item in section] == ["superseded-one"]
    assert [item["authority_version"] for item in section] == ["policy-2026.08.2"]


def test_compile_context_omits_the_section_when_nothing_is_active(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    _assert(mk, expires_at=_later(600))
    result = mk.compile_context("answer the user", 800, now=_later(600),
                                authority_budget=300)
    assert "authority" not in result["sections"]


def test_compile_context_hard_bounds_the_authority_section(tmp_path):
    mk = MemKraft(str(tmp_path))
    for index in range(MAX_ACTIVE_AUTHORITY + 2):
        _assert(mk, "authority-%d" % index, issued_at=_later(index),
                source_ref="hermes://policy/" + "s" * 400)
    result = mk.compile_context("answer the user", 4000, now=_later(100),
                                authority_budget=4000 - 1)
    section = result["sections"]["authority"]
    assert 0 < len(section) <= MAX_ACTIVE_AUTHORITY
    rendered = json.dumps(
        {"sections": {"authority": section}, "sources": []},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert mk.estimate_tokens(rendered) <= MAX_AUTHORITY_TOKENS
    assert result["estimated_tokens"] <= 4000


def test_compile_context_orders_authority_before_execution_and_execution_last(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    goal_id = "hermes/authority"
    mk.goal_declare(goal_id, "Compile", "Expose gates", [], [], now=NOW)
    mk.gate_declare(
        goal_id, "gate-00", "Required check",
        {"check_kind": "command", "check_ref": "pytest check"}, now=NOW)
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    _assert(mk)
    result = mk.compile_context(
        "answer the user", 3000, now=NOW, goal_id=goal_id,
        execution_budget=400, focus_budget=200, authority_budget=300)
    names = list(result["sections"])
    assert {"focus", "authority", "execution"} <= set(names)
    assert names[-1] == "execution"
    assert names.index("focus") < names.index("authority") < names.index("execution")
    assert result["estimated_tokens"] <= 3000


def test_authority_changes_the_usage_id_only_when_it_is_compiled(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    without = mk.compile_context("answer the user", 800, now=NOW, authority_budget=300)
    _assert(mk)
    with_authority = mk.compile_context("answer the user", 800, now=NOW,
                                        authority_budget=300)
    assert without["usage_id"] != with_authority["usage_id"]


def test_focus_output_is_unchanged_by_the_authority_feature(tmp_path):
    mk = MemKraft(str(tmp_path))
    _seed(mk)
    mk.focus_record("release", "cut 3.8.0", now=NOW, ttl_seconds=600)
    _assert(mk)
    with_focus_only = mk.compile_context("answer the user", 800, now=NOW,
                                         focus_budget=200)
    assert "authority" not in with_focus_only["sections"]
    assert [item["statement"] for item in with_focus_only["sections"]["focus"]] == [
        "cut 3.8.0"
    ]


def test_authority_states_projects_expired_status(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk, expires_at=_later(60))

    assert mk.authority_states(now=_later(60))[0]["status"] == "expired"


def test_authority_read_fails_closed_on_corrupt_jsonl(tmp_path):
    mk = MemKraft(str(tmp_path))
    _assert(mk)
    with mk._authority_events_path().open("a", encoding="utf-8") as stream:
        stream.write("{not-json}\n")

    with pytest.raises(AuthorityError, match="not structurally valid"):
        mk.authority_active(now=NOW)
