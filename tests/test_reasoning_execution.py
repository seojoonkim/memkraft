"""Security and behavior tests for deterministic ReasoningBank execution."""

from __future__ import annotations

import json
import math
import os
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

from memkraft import MemKraft, ReasoningAuthorization, ReasoningExecutionResult
from memkraft import reasoning_execution as execution


PROCEDURES = {
    "A.inclusion_exclusion_sum": (
        "Sum positive integers below 10 divisible by 3 or 5.",
        "23",
    ),
    "B.legendre_factorial_exponent": (
        "Find the trailing zeroes in 100 factorial.",
        "24",
    ),
    "C.shortest_grid_paths": (
        "Count shortest right/down paths across a 2 by 3 grid.",
        "10",
    ),
    "D.divisor_count_prime_powers": ("Count positive divisors of 2^3 * 5^2.", "12"),
    "E.sum_squares_or_cubes": ("Sum the squares from 1 through 5.", "55"),
    "F.modular_exponentiation": ("Compute 2^10 modulo 1000.", "24"),
}


@pytest.fixture
def mk(tmp_path: Path) -> MemKraft:
    return MemKraft(base_dir=str(tmp_path))


def seed(
    mk: MemKraft,
    task_id: str = "trusted-a",
    procedure_id: str = "A.inclusion_exclusion_sum",
):
    task, _ = PROCEDURES[procedure_id]
    mk.trajectory_start(task_id, title=task)
    mk.trajectory_log(
        task_id, 1, metadata={"procedure_ref": mk.reasoning_procedure_ref(procedure_id)}
    )
    mk.trajectory_complete(
        task_id, status="success", lesson="safe procedure", pattern_signature="safe"
    )
    auth = mk.reasoning_build_authorization([(task_id, procedure_id)])
    hit = next(
        item
        for item in mk.reasoning_recall(task, top_k=100, status="success")
        if item["task_id"] == task_id
    )
    return task, auth, hit, Path(hit["path"])


def route(
    mk: MemKraft, task: Any, auth: Any, hit: Any, fallback=lambda value: "fallback"
):
    calls = []
    original = mk.reasoning_recall

    def recall(query, **kwargs):
        calls.append((query, kwargs))
        if isinstance(hit, BaseException):
            raise hit
        return hit

    mk.reasoning_recall = recall
    try:
        result = mk.reasoning_execute(task, authorization=auth, fallback=fallback)
    finally:
        mk.reasoning_recall = original
    return result, calls


def rewrite(path: Path, records) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def records(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def assert_fallback(result: ReasoningExecutionResult, reason: str) -> None:
    assert result.route == "model_fallback"
    assert result.answer == "fallback"
    assert result.model_calls == 1
    assert result.reason == reason
    assert math.isfinite(result.latency_ms) and result.latency_ms >= 0


@pytest.mark.parametrize("task_id", ["a", "A_1-b", "x" * 120])
def test_builder_accepts_canonical_writer_task_ids(mk, task_id):
    seed(mk, task_id)


@pytest.mark.parametrize(
    "task_id", ["a--b", "-task", "task-", " task", "task ", "a.b", "-", "x" * 121]
)
def test_builder_rejects_noncanonical_writer_task_ids(mk, task_id):
    with pytest.raises(ValueError, match="canonical"):
        mk.reasoning_build_authorization([(task_id, "A.inclusion_exclusion_sum")])


def test_score_float_overflow_falls_back_exactly_once(mk):
    task, auth, hit, _ = seed(mk)
    seen = []
    result, _ = route(
        mk,
        task,
        auth,
        [{**hit, "score": 10**10000}],
        lambda value: seen.append(value) or "fallback",
    )
    assert_fallback(result, "recall_score_invalid")
    assert seen == [task]


def test_public_reference_and_immutable_types(mk):
    ref = mk.reasoning_procedure_ref("A.inclusion_exclusion_sum")
    assert set(ref) == {"id", "version", "registry_digest"}
    assert ref["id"] == "A.inclusion_exclusion_sum" and ref["version"] == 1
    assert len(ref["registry_digest"]) == 64
    with pytest.raises(KeyError):
        mk.reasoning_procedure_ref("unknown")
    auth = mk.reasoning_build_authorization([])
    assert isinstance(auth, ReasoningAuthorization)
    with pytest.raises(FrozenInstanceError):
        auth.seal = "0" * 64


@pytest.mark.parametrize("procedure_id", PROCEDURES)
def test_all_six_procedures_execute(mk, procedure_id):
    task, auth, hit, _ = seed(mk, "trusted-" + procedure_id[0].lower(), procedure_id)
    result, calls = route(mk, task, auth, [hit])
    assert result == replace(
        result,
        answer=PROCEDURES[procedure_id][1],
        route="executor",
        procedure_id=procedure_id,
        retrieval_score=hit["score"],
        model_calls=0,
        reason="executor_executed",
    )
    assert calls == [(task, {"top_k": 1, "status": "success"})]


@pytest.mark.parametrize(
    "procedure_id,task,answer",
    [
        (
            "B.legendre_factorial_exponent",
            "Find the exponent of 7 in the prime factorization of 100 factorial.",
            "16",
        ),
        ("E.sum_squares_or_cubes", "Sum the cubes from 1 through 5.", "225"),
    ],
)
def test_b_and_e_variants(mk, procedure_id, task, answer):
    _, auth, hit, _ = seed(mk, "variant", procedure_id)
    result, _ = route(mk, task, auth, [hit])
    assert result.route == "executor" and result.answer == answer


@pytest.mark.parametrize(
    "procedure_id,task,reason",
    [
        (
            "A.inclusion_exclusion_sum",
            "Sum positive integers below 10 divisible by 3 or 5",
            "grammar_mismatch",
        ),
        ("A.inclusion_exclusion_sum", "Do something unsupported.", "grammar_mismatch"),
        (
            "A.inclusion_exclusion_sum",
            "Sum positive integers below 10 divisible by 3 or 3.",
            "safety_constraint",
        ),
        (
            "B.legendre_factorial_exponent",
            "Find the exponent of 9 in the prime factorization of 100 factorial.",
            "safety_constraint",
        ),
        (
            "D.divisor_count_prime_powers",
            "Count positive divisors of 2^3 * 2^2.",
            "safety_constraint",
        ),
        (
            "C.shortest_grid_paths",
            "Count shortest right/down paths across a 1001 by 2 grid.",
            "safety_constraint",
        ),
        ("F.modular_exponentiation", "Compute 2^10 modulo 1.", "safety_constraint"),
    ],
)
def test_exact_grammar_and_resource_constraints_fallback(
    mk, procedure_id, task, reason
):
    _, auth, hit, _ = seed(mk, "constraint", procedure_id)
    result, _ = route(mk, task, auth, [hit])
    assert_fallback(result, reason)


def test_authorization_validation_and_empty_fallback(mk):
    assert mk.reasoning_build_authorization([]).entries == ()
    for value, error in [
        ("task", TypeError),
        (["task"], TypeError),
        ([("bad/id", "A.inclusion_exclusion_sum")], ValueError),
        ([("missing", "A.inclusion_exclusion_sum")], ValueError),
        ([("x", "unknown")], ValueError),
    ]:
        with pytest.raises(error):
            mk.reasoning_build_authorization(value)
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("x", "A.inclusion_exclusion_sum")] * 2)
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization(
            [
                (f"item-{index}", "A.inclusion_exclusion_sum")
                for index in range(execution.MAX_AUTHORIZATION_ENTRIES + 1)
            ]
        )
    task, _, hit, _ = seed(mk)
    result, _ = route(mk, task, mk.reasoning_build_authorization([]), [hit])
    assert_fallback(result, "authorization_missing")


def test_forged_mutated_stale_and_content_mutation_fail_closed(mk):
    task, auth, hit, path = seed(mk)
    forged = replace(auth, seal="0" * 64)
    result, _ = route(mk, task, forged, [hit])
    assert_fallback(result, "authorization_invalid")
    object.__setattr__(auth.entries[0], "procedure_id", "F.modular_exponentiation")
    result, _ = route(mk, task, auth, [hit])
    assert_fallback(result, "authorization_invalid")

    task, auth, hit, path = seed(mk, "trusted-second")
    path.write_bytes(path.read_bytes() + b"\n")
    result, _ = route(mk, task, auth, [hit])
    assert_fallback(result, "authorization_binding_mismatch")

    task, auth, hit, _ = seed(mk, "trusted-third")
    execution._rotate_authorization_key_for_tests()
    result, _ = route(mk, task, auth, [hit])
    assert_fallback(result, "authorization_invalid")


def test_recall_validation_and_exactly_once_fallback(mk):
    task, auth, hit, _ = seed(mk)
    cases = [
        ([], "recall_empty"),
        ("bad", "recall_malformed"),
        (["bad"], "recall_malformed"),
        ([{**hit, "status": "failure"}], "recall_malformed"),
        ([{**hit, "score": True}], "recall_score_invalid"),
        ([{**hit, "score": 0}], "recall_score_invalid"),
        ([{**hit, "score": float("nan")}], "recall_score_invalid"),
        (RuntimeError("secret retrieval"), "retrieval_error"),
    ]
    for recalled, reason in cases:
        seen = []
        result, _ = route(
            mk, task, auth, recalled, lambda value: seen.append(value) or "fallback"
        )
        assert_fallback(result, reason)
        assert seen == [task]
        assert "secret" not in result.reason


def test_malformed_task_fallback_and_fallback_contract(mk):
    _, auth, hit, _ = seed(mk)
    seen = []
    result, calls = route(
        mk, 123, auth, [hit], lambda value: seen.append(value) or "fallback"
    )
    assert_fallback(result, "task_invalid")
    assert seen == [123] and calls == []

    count = 0

    def explode(value):
        nonlocal count
        count += 1
        raise LookupError("model failed")

    with pytest.raises(LookupError, match="model failed"):
        route(mk, "wrong", auth, [hit], explode)
    assert count == 1
    count = 0

    def non_string(value):
        nonlocal count
        count += 1
        return 3

    with pytest.raises(TypeError, match="fallback answer must be a string"):
        route(mk, "wrong", auth, [hit], non_string)
    assert count == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda rs: rs.insert(0, dict(rs[0])),
        lambda rs: rs.append(dict(rs[-1])),
        lambda rs: rs.__setitem__(0, {**rs[0], "kind": "step", "metadata": {}}),
        lambda rs: rs.__setitem__(-1, {**rs[-1], "status": "failure"}),
        lambda rs: rs.insert(1, {"kind": "weird", "task_id": rs[0]["task_id"]}),
        lambda rs: rs.__setitem__(1, {**rs[1], "task_id": "other"}),
        lambda rs: rs.__setitem__(1, {**rs[1], "metadata": []}),
        lambda rs: rs.__setitem__(
            1,
            {
                **rs[1],
                "metadata": {"procedure_ref": {"id": "A.inclusion_exclusion_sum"}},
            },
        ),
        lambda rs: rs.insert(2, dict(rs[1])),
    ],
)
def test_builder_rejects_malformed_trajectory_shapes(mk, mutation):
    _, _, _, path = seed(mk)
    rs = records(path)
    mutation(rs)
    rewrite(path, rs)
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("trusted-a", "A.inclusion_exclusion_sum")])


@pytest.mark.parametrize(
    "record_index,key,value",
    [
        (0, "extra", 1),
        (0, "schema_version", True),
        (0, "schema_version", 2),
        (0, "title", 1),
        (0, "tags", [1]),
        (0, "started_at", "not-a-timestamp"),
        (1, "extra", 1),
        (1, "step", True),
        (1, "step", 0),
        (1, "thought", 1),
        (1, "action", 1),
        (1, "outcome", 1),
        (1, "ts", "not-a-timestamp"),
        (-1, "extra", 1),
        (-1, "lesson", 1),
        (-1, "pattern_signature", 1),
        (-1, "tags", [1]),
        (-1, "completed_at", "not-a-timestamp"),
    ],
)
def test_builder_enforces_exact_schema_v1_record_shapes(
    mk, record_index, key, value
):
    _, _, _, path = seed(mk)
    rs = records(path)
    rs[record_index][key] = value
    rewrite(path, rs)
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("trusted-a", "A.inclusion_exclusion_sum")])


@pytest.mark.parametrize("record_index,key", [(0, "title"), (1, "thought"), (-1, "lesson")])
def test_builder_rejects_missing_schema_fields(mk, record_index, key):
    _, _, _, path = seed(mk)
    rs = records(path)
    del rs[record_index][key]
    rewrite(path, rs)
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("trusted-a", "A.inclusion_exclusion_sum")])


@pytest.mark.parametrize("steps", [[2, 1], [1, 1]])
def test_builder_requires_strictly_increasing_steps(mk, steps):
    _, _, _, path = seed(mk)
    rs = records(path)
    second = {**rs[1], "step": steps[1], "metadata": {}}
    rs[1]["step"] = steps[0]
    rs.insert(2, second)
    rewrite(path, rs)
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("trusted-a", "A.inclusion_exclusion_sum")])


def test_builder_requires_at_least_one_step(mk):
    _, _, _, path = seed(mk)
    rs = records(path)
    rewrite(path, [rs[0], rs[-1]])
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("trusted-a", "A.inclusion_exclusion_sum")])


def test_builder_rejects_bool_procedure_version(mk):
    _, _, _, path = seed(mk)
    rs = records(path)
    rs[1]["metadata"]["procedure_ref"]["version"] = True
    rewrite(path, rs)
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("trusted-a", "A.inclusion_exclusion_sum")])


def test_duplicate_completion_and_old_trajectory(mk):
    task, _, _, path = seed(mk)
    rs = records(path)
    rs.append(dict(rs[-1]))
    rewrite(path, rs)
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("trusted-a", "A.inclusion_exclusion_sum")])

    mk.trajectory_start("old", title=task)
    mk.trajectory_log("old", 1, metadata={})
    mk.trajectory_complete(
        "old", status="success", lesson="old", pattern_signature="old"
    )
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("old", "A.inclusion_exclusion_sum")])


def test_paths_symlinks_directory_loop_oversize_and_bad_json(mk, tmp_path):
    task, auth, hit, path = seed(mk)
    variants = [
        {**hit, "path": str(tmp_path / "elsewhere.jsonl")},
        {**hit, "path": str(path.parent / ".." / "trajectories" / path.name)},
        {**hit, "path": str(path.parent)},
    ]
    outside = tmp_path / "elsewhere.jsonl"
    outside.write_bytes(path.read_bytes())
    for changed in variants:
        result, _ = route(mk, task, auth, [changed])
        assert_fallback(result, "trajectory_invalid")

    link = path.parent / "link.jsonl"
    link.symlink_to(path)
    changed = {**hit, "task_id": "link", "path": str(link)}
    result, _ = route(mk, task, auth, [changed])
    assert_fallback(result, "trajectory_invalid")
    loop = path.parent / "loop.jsonl"
    loop.symlink_to(loop)
    result, _ = route(mk, task, auth, [{**hit, "task_id": "loop", "path": str(loop)}])
    assert_fallback(result, "trajectory_invalid")

    path.write_bytes(b"x" * (execution.MAX_TRAJECTORY_BYTES + 1))
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("trusted-a", "A.inclusion_exclusion_sum")])
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("trusted-a", "A.inclusion_exclusion_sum")])


@pytest.mark.parametrize("component", ["base", ".memkraft", "trajectories"])
def test_builder_rejects_symlinked_trusted_root_component(tmp_path, component):
    real_base = tmp_path / "real-base"
    real_mk = MemKraft(base_dir=str(real_base))
    seed(real_mk)

    if component == "base":
        supplied_base = tmp_path / "linked-base"
        supplied_base.symlink_to(real_base, target_is_directory=True)
    elif component == ".memkraft":
        supplied_base = tmp_path / "supplied-base"
        supplied_base.mkdir()
        (supplied_base / ".memkraft").symlink_to(
            real_base / ".memkraft", target_is_directory=True
        )
    else:
        supplied_base = tmp_path / "supplied-base"
        (supplied_base / ".memkraft").mkdir(parents=True)
        (supplied_base / ".memkraft" / "trajectories").symlink_to(
            real_base / ".memkraft" / "trajectories", target_is_directory=True
        )

    linked_mk = MemKraft(base_dir=str(supplied_base))
    with pytest.raises(ValueError):
        linked_mk.reasoning_build_authorization(
            [("trusted-a", "A.inclusion_exclusion_sum")]
        )


def test_router_rejects_trusted_root_changed_to_symlink(mk, tmp_path):
    task, auth, hit, path = seed(mk)
    trajectories = path.parent
    moved = tmp_path / "moved-trajectories"
    trajectories.rename(moved)
    trajectories.symlink_to(moved, target_is_directory=True)
    result, _ = route(mk, task, auth, [hit])
    assert_fallback(result, "trajectory_invalid")


def test_hit_summary_binding_and_reason_codes_do_not_leak(mk):
    task, auth, hit, path = seed(mk)
    for key in ("title", "lesson", "pattern_signature", "task_id"):
        changed = {**hit, key: "private-secret"}
        result, _ = route(mk, task, auth, [changed])
        assert result.route == "model_fallback"
        assert "private-secret" not in result.reason
        assert str(path) not in result.reason


def test_static_product_source_has_no_forbidden_execution_constructs():
    source = Path(execution.__file__).read_text(encoding="utf-8").lower()
    forbidden = (
        "benchmark",
        "eval(",
        "exec(",
        "os.system",
        "subprocess",
        "shell",
        "expected",
        "answer_fn",
    )
    assert not [token for token in forbidden if token in source]
    assert "fullmatch" in source
    assert execution.__all__ == ["ReasoningAuthorization", "ReasoningExecutionResult"]


def test_result_is_immutable(mk):
    task, auth, hit, _ = seed(mk)
    result, _ = route(mk, task, auth, [hit])
    with pytest.raises(FrozenInstanceError):
        result.answer = "changed"
    assert os.path.isabs(auth.entries[0].path)


def test_relative_base_dir_executes_from_controlled_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    relative_mk = MemKraft(base_dir="relative/store")
    task, auth, hit, _ = seed(relative_mk)
    assert not os.path.isabs(hit["path"])
    assert os.path.isabs(auth.entries[0].path)
    result, _ = route(relative_mk, task, auth, [hit])
    assert result.route == "executor" and result.answer == "23"


def test_recall_requires_exact_builtin_list_and_dict(mk):
    task, auth, hit, _ = seed(mk)

    class HostileList(list):
        def __len__(self):
            raise AssertionError("list override invoked")

    class HostileSequence:
        def __len__(self):
            raise AssertionError("sequence override invoked")

        def __getitem__(self, index):
            raise AssertionError("sequence override invoked")

    class HostileDict(dict):
        def get(self, *args):
            raise AssertionError("dict override invoked")

    for recalled in (HostileList([hit]), HostileSequence(), [HostileDict(hit)]):
        result, _ = route(mk, task, auth, recalled)
        assert_fallback(result, "recall_malformed")


@pytest.mark.parametrize("deleted", ["entries", "seal"])
def test_deleted_authorization_fields_fail_closed(mk, deleted):
    task, auth, hit, _ = seed(mk)
    object.__delattr__(auth, deleted)
    result, _ = route(mk, task, auth, [hit])
    assert_fallback(result, "authorization_invalid")


def test_hostile_base_dir_and_path_values_fail_closed(mk):
    task, auth, hit, _ = seed(mk)

    class ExplodingBase:
        @property
        def base_dir(self):
            raise Exception("hostile base")

        def reasoning_recall(self, *args, **kwargs):
            return [hit]

    result = execution._reasoning_execute(ExplodingBase(), task, auth, lambda _: "fallback")
    assert_fallback(result, "trajectory_invalid")

    class ExplodingPath:
        def __fspath__(self):
            raise Exception("hostile path")

    result, _ = route(mk, task, auth, [{**hit, "path": ExplodingPath()}])
    assert_fallback(result, "trajectory_invalid")


def test_descriptor_traversal_uses_dir_fd_and_nofollow(mk, monkeypatch):
    task, auth, hit, _ = seed(mk)
    real_open = execution.os.open
    calls = []

    def recording_open(path, flags, *args, **kwargs):
        calls.append((path, flags, kwargs.get("dir_fd")))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(execution.os, "open", recording_open)
    result, _ = route(mk, task, auth, [hit])
    assert result.route == "executor"
    assert calls[0][2] is None
    assert [call[0] for call in calls[1:]] == [
        ".memkraft",
        "trajectories",
        "trusted-a.jsonl",
    ]
    assert all(call[2] is not None for call in calls[1:])
    assert all(call[1] & os.O_NOFOLLOW for call in calls)
    assert all(call[1] & os.O_DIRECTORY for call in calls[:3])


def test_base_path_swap_after_open_cannot_redirect_descriptor_walk(mk, tmp_path, monkeypatch):
    victim_mk = MemKraft(base_dir=str(tmp_path / "victim"))
    task, auth, hit, path = seed(victim_mk)
    base = Path(victim_mk.base_dir)
    moved = tmp_path / "original-moved"
    attacker = tmp_path / "attacker"
    attacker_mk = MemKraft(base_dir=str(attacker))
    seed(attacker_mk, "trusted-a")
    attacker_path = attacker / ".memkraft" / "trajectories" / "trusted-a.jsonl"
    attacker_path.write_text("attacker\n", encoding="utf-8")
    real_open = execution.os.open
    swapped = False

    def racing_open(name, flags, *args, **kwargs):
        nonlocal swapped
        descriptor = real_open(name, flags, *args, **kwargs)
        if not swapped and name == str(base) and kwargs.get("dir_fd") is None:
            swapped = True
            base.rename(moved)
            attacker.rename(base)
        return descriptor

    monkeypatch.setattr(execution.os, "open", racing_open)
    result, _ = route(victim_mk, task, auth, [hit])
    assert result.route == "executor" and result.answer == "23"
    assert path.name == "trusted-a.jsonl"


def test_deep_json_is_normalized_for_builder_and_route(mk):
    task, auth, hit, path = seed(mk)
    deep = b'{"kind":"start","x":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}\n"
    path.write_bytes(deep)
    with pytest.raises(ValueError):
        mk.reasoning_build_authorization([("trusted-a", "A.inclusion_exclusion_sum")])
    result, _ = route(mk, task, auth, [hit])
    assert result.route == "model_fallback" and result.model_calls == 1


def test_oversized_task_is_bounded_before_regex(mk):
    _, auth, hit, _ = seed(mk)
    task = "x" * (execution.MAX_TASK_CHARS + 1)
    result, _ = route(mk, task, auth, [hit])
    assert_fallback(result, "grammar_mismatch")


def test_empty_authorization_builds_without_descriptor_support(mk, monkeypatch):
    monkeypatch.setattr(execution, "_DESCRIPTOR_SUPPORTED", False)
    for completed in ([], iter(())):
        authorization = mk.reasoning_build_authorization(completed)
        assert authorization.entries == ()
        assert execution._authorization_valid(authorization)
    with pytest.raises(ValueError, match="descriptor traversal unsupported"):
        mk.reasoning_build_authorization(iter([("trusted-a", "A.inclusion_exclusion_sum")]))


@pytest.mark.parametrize("large", [False, True])
def test_task_requires_exact_str_without_invoking_subclass_overrides(mk, large):
    class HostileStr(str):
        def __len__(self):
            raise AssertionError("str override invoked")

        def __str__(self):
            raise AssertionError("str override invoked")

    task = HostileStr("x" * (execution.MAX_TASK_CHARS + 1 if large else 1))
    seen = []
    result, calls = route(
        mk, task, None, [], lambda value: seen.append(value) or "fallback"
    )
    assert_fallback(result, "task_invalid")
    assert seen == [task]
    assert calls == []


def test_authorization_fallback_exceptions_propagate_exactly_once(mk):
    task, auth, hit, _ = seed(mk)
    empty = mk.reasoning_build_authorization([])
    malformed = replace(auth)
    object.__delattr__(malformed, "entries")

    for authorization in (None, empty, malformed, replace(auth, seal="0" * 64)):
        calls = []

        def raising(value):
            calls.append(value)
            raise RuntimeError("fallback failed")

        with pytest.raises(RuntimeError, match="fallback failed"):
            route(mk, task, authorization, [hit], raising)
        assert calls == [task]


def test_authorization_non_string_fallback_fails_exactly_once(mk):
    task, auth, hit, _ = seed(mk)
    malformed = replace(auth)
    object.__delattr__(malformed, "seal")

    cases = (None, mk.reasoning_build_authorization([]), malformed, replace(auth, seal="x"))
    for authorization in cases:
        calls = []

        def non_string(value):
            calls.append(value)
            return object()

        with pytest.raises(TypeError, match="fallback answer must be a string"):
            route(mk, task, authorization, [hit], non_string)
        assert calls == [task]


def test_descriptor_cleanup_attempts_every_close_after_one_fails(mk, monkeypatch):
    seed(mk)
    real_close = execution.os.close
    closed = []

    def failing_close(descriptor):
        closed.append(descriptor)
        real_close(descriptor)
        if len(closed) == 1:
            raise OSError("close failed")

    monkeypatch.setattr(execution.os, "close", failing_close)
    with pytest.raises(OSError, match="close failed"):
        execution._read_anchored(mk.base_dir, "trusted-a")
    assert len(closed) == 4
    assert len(set(closed)) == 4


def test_descriptor_close_error_does_not_mask_primary_error(mk, monkeypatch):
    seed(mk)
    real_close = execution.os.close
    closed = []

    def failing_read(*args, **kwargs):
        raise ValueError("read failed")

    def failing_close(descriptor):
        closed.append(descriptor)
        real_close(descriptor)
        if len(closed) == 1:
            raise OSError("close failed")

    monkeypatch.setattr(execution.os, "read", failing_read)
    monkeypatch.setattr(execution.os, "close", failing_close)
    with pytest.raises(ValueError, match="read failed"):
        execution._read_anchored(mk.base_dir, "trusted-a")
    assert len(closed) == 4
