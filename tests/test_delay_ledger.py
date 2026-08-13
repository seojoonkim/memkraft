"""Delay timing ledger preview contract."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft import delay_ledger
from memkraft.execution_protocol import ExecutionError

NOW = "2026-08-13T09:00:00Z"


def _mk(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    return mk


def _path(mk):
    return Path(mk.base_dir) / ".memkraft" / "delay" / "events.jsonl"


def _finish_sample(mk, number, elapsed, subject="build", kind="task"):
    run_id = "run-%03d" % number
    mk.delay_run_start(run_id, kind, subject, now=NOW,
                       operation_id="start-%03d" % number)
    return mk.delay_run_finish(run_id, elapsed, now=NOW,
                               operation_id="finish-%03d" % number)


def _anomalous_run(mk, subject="build"):
    for number in range(5):
        _finish_sample(mk, number, 10, subject=subject)
    return _finish_sample(mk, 99, 11, subject=subject)


def test_hierarchy_and_finish_are_append_only(tmp_path):
    mk = _mk(tmp_path)
    task = mk.delay_run_start("task-001", "task", "release", now=NOW)
    phase = mk.delay_run_start("phase-001", "phase", "tests", now=NOW,
                               parent_run_id="task-001")
    attempt = mk.delay_run_start("attempt-001", "attempt", "pytest", now=NOW,
                                 parent_run_id="phase-001")
    finish = mk.delay_run_finish("attempt-001", 321, now=NOW,
                                 evidence_refs=["ci:job-7"])

    assert [task["event_seq"], phase["event_seq"], attempt["event_seq"],
            finish["event_seq"]] == [1, 2, 3, 4]
    assert finish["record"]["elapsed_ms"] == 321
    assert finish["record"]["privacy"] == "local_private"
    assert finish["record"]["authority_verified"] is False
    assert len(_path(mk).read_text().splitlines()) == 4


@pytest.mark.parametrize("kind,parent", [("task", "task-001"),
                                          ("phase", None),
                                          ("attempt", None)])
def test_invalid_hierarchy_fails_closed(tmp_path, kind, parent):
    mk = _mk(tmp_path)
    if parent:
        mk.delay_run_start(parent, "task", "root", now=NOW)
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_start("child-001", kind, "subject", now=NOW,
                           parent_run_id=parent)
    assert excinfo.value.code == "E_DELAY_HIERARCHY"


def test_finish_requires_open_run_and_only_finishes_once(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_finish("missing-001", 1, now=NOW)
    assert excinfo.value.code == "E_DELAY_NOT_FOUND"
    mk.delay_run_start("run-001", "task", "build", now=NOW)
    mk.delay_run_finish("run-001", 10, now=NOW)
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_finish("run-001", 11, now=NOW)
    assert excinfo.value.code == "E_DELAY_FINISHED"


def test_elapsed_is_non_negative_integer_not_bool(tmp_path):
    mk = _mk(tmp_path)
    mk.delay_run_start("run-001", "task", "build", now=NOW)
    for value in (-1, 1.5, True):
        with pytest.raises(ExecutionError) as excinfo:
            mk.delay_run_finish("run-001", value, now=NOW)
        assert excinfo.value.code == "E_DELAY_VALIDATION"


def test_outcome_is_closed_and_only_completed_runs_feed_eta(tmp_path):
    mk = _mk(tmp_path)
    for number, outcome in enumerate([
        "completed", "completed", "completed", "completed", "completed",
        "failed", "interrupted", "incomplete", "aborted",
    ]):
        run_id = "outcome-%03d" % number
        mk.delay_run_start(run_id, "task", "build", now=NOW)
        mk.delay_run_finish(run_id, 1000 + number, now=NOW, outcome=outcome)
    estimate = mk.delay_estimate("task", "build", now=NOW)
    assert estimate["sample_count"] == 5
    assert estimate["p50_ms"] == 1002
    assert "samples_ms" not in estimate
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_start("outcome-invalid", "task", "build", now=NOW)
        mk.delay_run_finish("outcome-invalid", 1, now=NOW, outcome="unknown")
    assert excinfo.value.code == "E_DELAY_VALIDATION"


def test_exact_operation_id_retry_and_mismatch(tmp_path):
    mk = _mk(tmp_path)
    first = mk.delay_run_start("run-001", "task", "build", now=NOW,
                               operation_id="same-op")
    again = mk.delay_run_start("run-001", "task", "build", now=NOW,
                               operation_id="same-op")
    assert again["outcome"] == "already_applied"
    assert again["record_id"] == first["record_id"]
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_start("run-002", "task", "build", now=NOW,
                           operation_id="same-op")
    assert excinfo.value.code == "E_DELAY_IDEMPOTENCY_MISMATCH"


def test_concurrent_starts_allocate_unique_contiguous_sequences(tmp_path):
    mk = _mk(tmp_path)
    def start(number):
        return mk.delay_run_start("run-%03d" % number, "task", "build", now=NOW,
                                  operation_id="op-%03d" % number)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(start, range(20)))
    assert sorted(item["event_seq"] for item in results) == list(range(1, 21))


def test_corrupt_or_inconsistent_history_makes_eta_unavailable_and_blocks_writes(tmp_path):
    mk = _mk(tmp_path)
    for number in range(5):
        _finish_sample(mk, number, 100 + number)
    with _path(mk).open("a", encoding="utf-8") as handle:
        handle.write("{broken\n")
    eta = mk.delay_estimate("task", "build", now=NOW)
    assert eta["available"] is False
    assert eta["reason"] == "corrupt_history"
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_start("run-999", "task", "build", now=NOW)
    assert excinfo.value.code == "E_DELAY_LOG_CORRUPT"


def test_eta_needs_five_samples_and_uses_nearest_rank_quantiles(tmp_path):
    mk = _mk(tmp_path)
    for number, elapsed in enumerate([10, 20, 30, 40]):
        _finish_sample(mk, number, elapsed)
    assert mk.delay_estimate("task", "build", now=NOW)["available"] is False
    _finish_sample(mk, 4, 100)
    eta = mk.delay_estimate("task", "build", now=NOW)
    assert eta["available"] is True
    assert eta["sample_count"] == 5
    assert eta["p50_ms"] == 30
    assert eta["p80_ms"] == 40
    assert eta["mad_ms"] == 10
    assert eta["anomaly_threshold_ms"] == 70  # p50 + 4 * integer MAD


def test_eta_window_bounds_and_through_seq_are_deterministic(tmp_path):
    mk = _mk(tmp_path)
    finishes = [_finish_sample(mk, i, value) for i, value in
                enumerate([10, 20, 30, 40, 50, 60])]
    through = finishes[4]["event_seq"]
    eta = mk.delay_estimate("task", "build", now=NOW, window=5,
                            through_seq=through)
    assert eta["through_seq"] == through
    assert eta["p50_ms"] == 30
    assert eta["p80_ms"] == 40
    with pytest.raises(ExecutionError):
        mk.delay_estimate("task", "build", now=NOW, window=4)
    with pytest.raises(ExecutionError):
        mk.delay_estimate("task", "build", now=NOW, window=1001)


def test_eta_samples_are_scoped_to_exact_kind_and_subject(tmp_path):
    mk = _mk(tmp_path)
    for number, elapsed in enumerate([10, 20, 30, 40, 50]):
        _finish_sample(mk, number, elapsed, subject="build")
    for number, elapsed in enumerate([1000, 2000, 3000, 4000, 5000], start=10):
        _finish_sample(mk, number, elapsed, subject="deploy")

    assert mk.delay_estimate("task", "build", now=NOW)["p50_ms"] == 30
    assert mk.delay_estimate("task", "deploy", now=NOW)["p50_ms"] == 3000


def test_timestamps_are_canonicalized_and_invalid_values_fail_closed(tmp_path):
    mk = _mk(tmp_path)
    started = mk.delay_run_start("run-001", "task", "build",
                                 now="2026-08-13T09:00:00+00:00")
    assert started["record"]["created_at"] == NOW
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_start("run-002", "task", "build", now="not-a-time")
    assert excinfo.value.code == "E_DELAY_VALIDATION"


def test_anomaly_uses_integer_mad_and_zero_mad_still_flags_larger_value(tmp_path):
    mk = _mk(tmp_path)
    for i, value in enumerate([10, 10, 10, 10, 10, 11]):
        result = _finish_sample(mk, i, value)
    assert result["record"]["anomaly"] is True
    assert result["record"]["anomaly_threshold_ms"] == 10


def test_receipt_chain_is_explicit_inert_evidence(tmp_path):
    mk = _mk(tmp_path)
    anomalous = _anomalous_run(mk)
    retro = mk.delay_record_retrospective(
        "retro-001", anomalous["record"]["run_id"], "cold-cache", now=NOW,
        evidence_refs=["trace:opaque"],
    )
    action = mk.delay_record_action(
        "action-001", "retro-001", "warm-cache", now=NOW,
    )
    application = mk.delay_record_application(
        "application-001", "action-001", "applied-in-host", now=NOW,
        external_receipt_ref="host:change-8",
    )
    verification = mk.delay_record_verification(
        "verification-001", "application-001", "pass", now=NOW,
        evidence_refs=["ci:green"],
    )
    assert [action["event_seq"], application["event_seq"], verification["event_seq"]] == [
        retro["event_seq"] + 1, retro["event_seq"] + 2, retro["event_seq"] + 3,
    ]
    assert retro["record"]["trigger_finish_seq"] == anomalous["event_seq"]
    assert retro["record"]["trigger_algorithm"] == "nearest-rank-p50-plus-4mad-v1"
    assert verification["record"]["verdict"] == "pass"
    assert verification["record"]["authority_verified"] is False
    assert not any(key in verification["record"] for key in
                   ("command", "workflow", "scheduler", "execute"))


def test_receipt_chain_requires_exact_predecessor_type(tmp_path):
    mk = _mk(tmp_path)
    anomalous = _anomalous_run(mk)
    mk.delay_record_retrospective(
        "retro-001", anomalous["record"]["run_id"], "cold-cache", now=NOW,
    )
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_record_application("application-001", "retro-001", "claim", now=NOW)
    assert excinfo.value.code == "E_DELAY_CHAIN"


def test_retrospective_requires_anomaly_and_rejects_duplicate_without_mutation(tmp_path):
    mk = _mk(tmp_path)
    mk.delay_run_start("normal-001", "task", "build", now=NOW)
    mk.delay_run_finish("normal-001", 10, now=NOW)
    for run_id in ("missing-001", "normal-001"):
        with pytest.raises(ExecutionError) as excinfo:
            mk.delay_record_retrospective("retro-denied", run_id, "cold-cache", now=NOW)
        assert excinfo.value.code == "E_DELAY_CHAIN"
    anomalous = _anomalous_run(mk, subject="deploy")
    mk.delay_record_retrospective(
        "retro-001", anomalous["record"]["run_id"], "cold-cache", now=NOW,
    )
    before = len(_path(mk).read_text().splitlines())
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_record_retrospective(
            "retro-001", anomalous["record"]["run_id"], "cold-cache", now=NOW,
            operation_id="different-operation",
        )
    assert excinfo.value.code == "E_DELAY_CHAIN"
    assert len(_path(mk).read_text().splitlines()) == before


def test_parent_child_lifecycle_is_fail_closed(tmp_path):
    mk = _mk(tmp_path)
    mk.delay_run_start("task-001", "task", "build", now=NOW)
    mk.delay_run_start("phase-001", "phase", "build:active", now=NOW,
                       parent_run_id="task-001")
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_finish("task-001", 5, now=NOW)
    assert excinfo.value.code == "E_DELAY_HIERARCHY"
    mk.delay_run_finish("phase-001", 5, now=NOW)
    mk.delay_run_finish("task-001", 5, now=NOW)
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_start("phase-002", "phase", "build:wait", now=NOW,
                           parent_run_id="task-001")
    assert excinfo.value.code == "E_DELAY_HIERARCHY"


def test_privacy_authority_opaque_tokens_and_owner_only_durability(tmp_path):
    mk = _mk(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_start("run-001", "task", "build", now=NOW,
                           authority_verified=True)
    assert excinfo.value.code == "E_AUTHORITY_VERIFIED_FORBIDDEN"
    for kwargs in (
        {"subject": "raw user transcript"},
        {"subject": "build", "context_refs": ["Bearer secret value"]},
        {"subject": "build", "context_refs": ["x" * 257]},
    ):
        with pytest.raises(ExecutionError) as excinfo:
            mk.delay_run_start("run-001", "task", now=NOW, **kwargs)
        assert excinfo.value.code == "E_DELAY_VALIDATION"
    mk.delay_run_start("secure-001", "task", "build", now=NOW)
    assert _path(mk).stat().st_mode & 0o777 == 0o600


def test_existing_permissive_ledger_is_repaired_before_append(tmp_path, monkeypatch):
    mk = _mk(tmp_path)
    path = _path(mk)
    path.parent.mkdir(parents=True)
    path.touch(mode=0o644)
    seen_modes = []
    real_append = delay_ledger.append

    def checking_append(target, record):
        seen_modes.append(Path(target).stat().st_mode & 0o777)
        return real_append(target, record)

    monkeypatch.setattr(delay_ledger, "append", checking_append)
    mk.delay_run_start("secure-001", "task", "build", now=NOW)
    assert seen_modes == [0o600]
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_retrospective_auto_id_retry_uses_trigger_snapshot(tmp_path):
    mk = _mk(tmp_path)
    anomalous = _anomalous_run(mk)
    first = mk.delay_record_retrospective(
        "retro-001", anomalous["record"]["run_id"], "cold-cache", now=NOW,
    )
    retry = mk.delay_record_retrospective(
        "retro-001", anomalous["record"]["run_id"], "cold-cache", now=NOW,
    )
    assert retry["outcome"] == "already_applied"
    assert retry["operation_id"] == first["operation_id"]
    assert retry["record_fingerprint"] == first["record_fingerprint"]


def test_static_boundary_and_additive_python_api_only():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/memkraft/delay_ledger.py").read_text()
    forbidden = ("subprocess", "requests", "urllib", "socket", "schedule",
                 "opentelemetry", "mcp", "cli")
    assert not any(token in source.lower() for token in forbidden)
    init_source = (root / "src/memkraft/__init__.py").read_text()
    assert "DelayLedgerMixin" in init_source
    assert "DelayLedgerMixin," in init_source
    mk = MemKraft.__new__(MemKraft)
    assert callable(getattr(mk, "delay_run_start"))
    assert callable(getattr(mk, "delay_estimate"))


def test_parseable_inconsistent_finish_is_fail_closed(tmp_path):
    mk = _mk(tmp_path)
    path = _path(mk)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"record_type": "delay_run_finish", "event_seq": 1,
                                "run_id": "unknown", "elapsed_ms": 5}) + "\n")
    eta = mk.delay_estimate("task", "build", now=NOW)
    assert eta["available"] is False
    assert eta["reason"] == "corrupt_history"
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_start("run-001", "task", "build", now=NOW)
    assert excinfo.value.code == "E_DELAY_LOG_CORRUPT"


@pytest.mark.parametrize("field,value", [
    ("run_id", []),
    ("parent_run_id", {}),
])
def test_unhashable_identity_is_fail_closed(tmp_path, field, value):
    mk = _mk(tmp_path)
    mk.delay_run_start("seed-001", "task", "build", now=NOW)
    path = _path(mk)
    record = {
        "id": "record-002", "schema_version": 1, "created_at": NOW,
        "record_type": "delay_run_start", "event_seq": 2,
        "operation_id": "operation-002", "privacy": "local_private",
        "authority_verified": False, "evidence_refs": [],
        "run_id": "run-001", "kind": "phase", "subject": "build",
        "parent_run_id": "seed-001",
    }
    record[field] = value
    with path.open("a") as handle:
        handle.write(json.dumps(record) + "\n")
    assert mk.delay_estimate("task", "build", now=NOW) == {
        "available": False, "reason": "corrupt_history"
    }
    with pytest.raises(ExecutionError) as excinfo:
        mk.delay_run_start("next-001", "task", "build", now=NOW)
    assert excinfo.value.code == "E_DELAY_LOG_CORRUPT"


def test_empty_read_does_not_create_ledger(tmp_path):
    mk = _mk(tmp_path)
    result = mk.delay_estimate("task", "build", now=NOW)
    assert result["available"] is False
    assert result["reason"] == "insufficient_samples"
    assert not _path(mk).exists()
