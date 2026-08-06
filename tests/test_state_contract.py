"""State Contract — deterministic project-state drift detection (runtime-neutral).

Covers the real 2026-08-06 incident fixture (four findings, fail closed), a
healthy fixture, determinism of finding IDs / report digest, dry-run zero-write,
append + idempotency + payload-mismatch fail-closed, corrupt-log fail-closed,
and the host adapter (subprocess/importlib fully faked — no network, no git).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft import state_contract as sc

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_adapter():
    path = REPO_ROOT / "scripts" / "check_project_state.py"
    spec = importlib.util.spec_from_file_location("_check_project_state", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def adapter():
    return _load_adapter()


# --------------------------------------------------------------------------
# fixtures: the real 2026-08-06 incident and a healthy release state
# --------------------------------------------------------------------------

def incident_observations():
    """The observed 2026-08-06 distributed project-state drift."""
    return {
        "source": {"version": "3.3.0"},
        "runtime": {
            "version": "3.3.0",
            "source_path": "/private/tmp/memkraft-3.3/src/memkraft/__init__.py",
        },
        "distribution": {"version": "3.2.0"},
        "public": {"version": "3.2.0"},
        "git": {
            "branch": "release/3.3.0",
            "tree_clean": True,
            "branch_remote_backed": False,
        },
        "snapshot": {"public_version": "3.1.0", "development_version": "3.2.0"},
    }


def healthy_observations():
    return {
        "source": {"version": "3.3.0"},
        "runtime": {
            "version": "3.3.0",
            "source_path": "/Users/dev/work/memkraft/src/memkraft/__init__.py",
        },
        "distribution": {"version": "3.3.0"},
        "public": {"version": "3.3.0"},
        "git": {
            "branch": "release/3.3.0",
            "tree_clean": True,
            "branch_remote_backed": True,
        },
        "snapshot": {"public_version": "3.3.0", "development_version": "3.3.0"},
    }


# --------------------------------------------------------------------------
# core evaluation
# --------------------------------------------------------------------------

def test_incident_fixture_fails_closed_with_four_findings():
    report = sc.evaluate(incident_observations())

    assert report["release_ready"] is False
    assert len(report["findings"]) == 4

    by_constraint = {f["constraint_id"]: f for f in report["findings"]}
    assert set(by_constraint) == {
        "version_alignment",
        "durable_source_path",
        "release_branch_remote_backed",
        "project_snapshot_current",
    }

    version = by_constraint["version_alignment"]
    assert version["kind"] == "equals"
    assert version["severity"] == "critical"
    assert version["reason"] == "mismatch"
    assert version["observed"] == {
        "distribution.version": "3.2.0",
        "runtime.version": "3.3.0",
        "source.version": "3.3.0",
    }

    path_finding = by_constraint["durable_source_path"]
    assert path_finding["kind"] == "forbidden_path_prefixes"
    assert path_finding["severity"] == "critical"
    assert path_finding["reason"] == "forbidden_path_prefix"
    assert path_finding["matched_prefix"] == "/private/tmp"

    branch = by_constraint["release_branch_remote_backed"]
    assert branch["kind"] == "truthy"
    assert branch["reason"] == "falsy"
    assert branch["severity"] in ("critical", "error")

    snapshot = by_constraint["project_snapshot_current"]
    assert snapshot["kind"] == "equals"
    assert snapshot["reason"] == "mismatch"
    assert snapshot["observed"]["snapshot.public_version"] == "3.1.0"
    assert snapshot["observed"]["snapshot.development_version"] == "3.2.0"

    assert report["counts"]["critical"] >= 1
    assert report["counts"]["error"] >= 1


def test_healthy_fixture_is_release_ready():
    report = sc.evaluate(healthy_observations())
    assert report["findings"] == []
    assert report["release_ready"] is True
    assert report["counts"] == {"critical": 0, "error": 0, "warning": 0}


def test_pre_release_snapshot_tracks_public_and_development_versions_separately():
    observations = healthy_observations()
    observations["source"]["version"] = "3.4.0"
    observations["runtime"]["version"] = "3.4.0"
    observations["distribution"]["version"] = "3.4.0"
    observations["snapshot"]["development_version"] = "3.4.0"
    observations["public"]["version"] = "3.3.0"
    observations["snapshot"]["public_version"] = "3.3.0"

    report = sc.evaluate(observations)

    assert report["release_ready"] is True
    assert report["findings"] == []


def test_dirty_working_tree_blocks_release():
    observations = healthy_observations()
    observations["git"]["tree_clean"] = False

    report = sc.evaluate(observations)

    assert report["release_ready"] is False
    assert [finding["constraint_id"] for finding in report["findings"]] == [
        "working_tree_clean"
    ]


def test_only_warning_findings_stay_release_ready():
    constraints = [
        {
            "id": "advisory",
            "kind": "truthy",
            "severity": "warning",
            "paths": ["git.tree_clean"],
        }
    ]
    report = sc.evaluate({"git": {"tree_clean": False}}, constraints)
    assert len(report["findings"]) == 1
    assert report["release_ready"] is True


def test_missing_observation_fails_closed():
    constraints = [
        {
            "id": "needs_value",
            "kind": "truthy",
            "severity": "error",
            "paths": ["git.branch_remote_backed"],
        }
    ]
    report = sc.evaluate({"git": {}}, constraints)
    assert report["findings"][0]["reason"] == "missing_observation"
    assert report["release_ready"] is False


def test_unsupported_constraint_kind_is_rejected():
    with pytest.raises(ValueError):
        sc.evaluate({}, [{"id": "x", "kind": "regex", "severity": "error", "paths": ["a"]}])


def test_invalid_severity_is_rejected():
    with pytest.raises(ValueError):
        sc.evaluate({}, [{"id": "x", "kind": "truthy", "severity": "info", "paths": ["a"]}])


def test_findings_and_digest_are_insertion_order_independent():
    a = incident_observations()
    b = json.loads(json.dumps(a))
    # rebuild every mapping in reverse key order
    def reverse(node):
        if isinstance(node, dict):
            return {k: reverse(node[k]) for k in reversed(list(node))}
        return node

    report_a = sc.evaluate(a)
    report_b = sc.evaluate(reverse(b))

    assert report_a["digest"] == report_b["digest"]
    assert [f["id"] for f in report_a["findings"]] == [f["id"] for f in report_b["findings"]]
    # findings are ordered deterministically: severity rank, then constraint id
    order = [(f["severity"], f["constraint_id"]) for f in report_a["findings"]]
    assert order == sorted(order, key=lambda t: (sc.SEVERITY_ORDER.index(t[0]), t[1]))


def test_digest_changes_when_observations_change():
    healthy = sc.evaluate(healthy_observations())
    incident = sc.evaluate(incident_observations())
    assert healthy["digest"] != incident["digest"]


def test_healthy_digest_binds_the_exact_observations():
    first = healthy_observations()
    second = healthy_observations()
    second["source"]["version"] = "3.4.0"
    second["runtime"]["version"] = "3.4.0"
    second["distribution"]["version"] = "3.4.0"
    second["public"]["version"] = "3.4.0"
    second["snapshot"]["public_version"] = "3.4.0"
    second["snapshot"]["development_version"] = "3.4.0"

    report_first = sc.evaluate(first)
    report_second = sc.evaluate(second)

    assert report_first["release_ready"] is True
    assert report_second["release_ready"] is True
    assert report_first["observation_digest"] != report_second["observation_digest"]
    assert report_first["digest"] != report_second["digest"]


def test_forbidden_prefix_requires_a_path_component_boundary():
    observations = healthy_observations()
    observations["runtime"]["source_path"] = "/tmpfoo/memkraft/__init__.py"

    report = sc.evaluate(observations)

    assert report["release_ready"] is True
    assert report["findings"] == []


def test_finding_ids_are_stable_across_runs():
    first = sc.evaluate(incident_observations())
    second = sc.evaluate(incident_observations())
    assert [f["id"] for f in first["findings"]] == [f["id"] for f in second["findings"]]


def test_evaluate_does_not_mutate_observations():
    obs = incident_observations()
    snapshot = json.loads(json.dumps(obs))
    sc.evaluate(obs)
    assert obs == snapshot


# --------------------------------------------------------------------------
# mixin: dry-run, record, idempotency, corrupt log
# --------------------------------------------------------------------------

@pytest.fixture()
def mem(tmp_path):
    return MemKraft(base_dir=str(tmp_path / "mem"))


def _log_path(mem):
    return Path(mem.base_dir) / ".memkraft" / "state_contract" / "events.jsonl"


def test_dry_run_evaluation_writes_nothing(mem):
    before = sorted(p.relative_to(mem.base_dir).as_posix() for p in Path(mem.base_dir).rglob("*"))
    report = mem.state_contract_check(incident_observations())
    after = sorted(p.relative_to(mem.base_dir).as_posix() for p in Path(mem.base_dir).rglob("*"))

    assert report["release_ready"] is False
    assert before == after
    assert not _log_path(mem).exists()
    assert not _log_path(mem).parent.exists()


def test_record_appends_one_immutable_report(mem):
    obs = incident_observations()
    record = mem.state_contract_record(obs, operation_id="op-1")

    assert record["operation_id"] == "op-1"
    assert record["report"]["release_ready"] is False
    lines = _log_path(mem).read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1

    history = mem.state_contract_history()
    assert history["skipped"] == 0
    assert len(history["records"]) == 1
    assert history["records"][0]["report"]["digest"] == record["report"]["digest"]


def test_record_is_idempotent_for_exact_retry(mem):
    obs = incident_observations()
    first = mem.state_contract_record(obs, operation_id="op-1")
    second = mem.state_contract_record(obs, operation_id="op-1")

    assert second["id"] == first["id"]
    assert len(_log_path(mem).read_text(encoding="utf-8").strip().split("\n")) == 1


def test_record_same_operation_id_different_payload_fails_closed(mem):
    mem.state_contract_record(incident_observations(), operation_id="op-1")
    with pytest.raises(ValueError):
        mem.state_contract_record(healthy_observations(), operation_id="op-1")
    assert len(_log_path(mem).read_text(encoding="utf-8").strip().split("\n")) == 1


def test_record_same_operation_id_different_healthy_state_fails_closed(mem):
    first = healthy_observations()
    second = healthy_observations()
    for section in ("source", "runtime", "distribution", "public"):
        second[section]["version"] = "3.4.0"
    second["snapshot"]["public_version"] = "3.4.0"
    second["snapshot"]["development_version"] = "3.4.0"

    mem.state_contract_record(first, operation_id="op-1")
    with pytest.raises(ValueError):
        mem.state_contract_record(second, operation_id="op-1")


def test_record_closes_governance_descriptor(mem, monkeypatch):
    closed = []
    governance_fd = 999
    monkeypatch.setattr(mem, "_governance_lock", lambda: governance_fd)
    monkeypatch.setattr(sc, "_unlock", lambda fd: None)
    monkeypatch.setattr(sc.os, "close", lambda fd: closed.append(fd))

    mem.state_contract_record(healthy_observations(), operation_id="op-1")

    assert governance_fd in closed


def test_record_closes_descriptor_even_if_unlock_raises(mem, monkeypatch):
    closed = []
    governance_fd = 999
    monkeypatch.setattr(mem, "_governance_lock", lambda: governance_fd)
    monkeypatch.setattr(sc, "_unlock", lambda fd: (_ for _ in ()).throw(OSError("unlock")))
    monkeypatch.setattr(sc.os, "close", lambda fd: closed.append(fd))

    with pytest.raises(OSError, match="unlock"):
        mem.state_contract_record(healthy_observations(), operation_id="op-1")

    assert governance_fd in closed


def test_history_limit_zero_is_empty_and_negative_is_rejected(mem):
    mem.state_contract_record(healthy_observations(), operation_id="op-1")
    assert mem.state_contract_history(limit=0)["records"] == []
    with pytest.raises(ValueError):
        mem.state_contract_history(limit=-1)


def test_corrupt_log_fails_closed_on_write_and_is_surfaced_on_read(mem):
    mem.state_contract_record(healthy_observations(), operation_id="op-1")
    with _log_path(mem).open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")

    history = mem.state_contract_history()
    assert history["skipped"] == 1
    assert len(history["records"]) == 1

    with pytest.raises(sc.StateContractLogCorrupt):
        mem.state_contract_record(incident_observations(), operation_id="op-2")


def test_core_module_has_no_side_effecting_imports():
    source = (REPO_ROOT / "src" / "memkraft" / "state_contract.py").read_text(encoding="utf-8")
    for banned in ("subprocess", "importlib.metadata", "socket", "urllib", "shutil.rmtree", "os.system"):
        assert banned not in source


# --------------------------------------------------------------------------
# host adapter (no network, no real git, no real pip)
# --------------------------------------------------------------------------

def test_adapter_builds_incident_observations(adapter):
    git = {
        "branch": "release/3.3.0",
        "tree_clean": True,
        "branch_remote_backed": False,
    }
    runtime = {
        "version": "3.3.0",
        "source_path": "/private/tmp/memkraft-3.3/src/memkraft/__init__.py",
        "distribution_version": "3.2.0",
    }
    obs = adapter.build_observations(
        git=git,
        runtime=runtime,
        source_version="3.3.0",
        public_version="3.2.0",
        snapshot_public_version="3.1.0",
        snapshot_development_version="3.2.0",
    )
    report = sc.evaluate(obs)
    assert report["release_ready"] is False
    assert len(report["findings"]) == 4


def test_adapter_collect_git_uses_read_only_commands(adapter):
    calls = []

    def fake_runner(args):
        calls.append(list(args))
        joined = " ".join(args)
        if "status" in joined:
            return ""
        if "@{upstream}" in joined:
            return ""
        if "--abbrev-ref" in joined:
            return "release/3.3.0"
        raise AssertionError("unexpected command: " + joined)

    git = adapter.collect_git(Path("/repo"), runner=fake_runner)
    assert git["branch"] == "release/3.3.0"
    assert git["tree_clean"] is True
    assert git["branch_remote_backed"] is False

    mutating = ("commit", "push", "tag", "checkout", "reset", "fetch", "ls-remote", "pull")
    for call in calls:
        assert not set(call) & set(mutating), call


def test_adapter_collect_git_detects_remote_backed_branch(adapter):
    def fake_runner(args):
        joined = " ".join(args)
        if "status" in joined:
            return " M src/memkraft/core.py"
        if "@{upstream}..HEAD" in joined:
            return "0"
        if "@{upstream}" in joined:
            return "origin/release/3.3.0"
        if "ls-remote" in joined:
            return "abc123\trefs/heads/release/3.3.0"
        if joined.endswith("rev-parse HEAD"):
            return "abc123"
        if "--abbrev-ref" in joined:
            return "release/3.3.0"
        raise AssertionError("unexpected command: " + joined)

    git = adapter.collect_git(Path("/repo"), runner=fake_runner)
    assert git["branch_remote_backed"] is True
    assert git["tree_clean"] is False


def test_adapter_detached_head_is_not_remote_backed(adapter):
    def fake_runner(args):
        joined = " ".join(args)
        if "status" in joined:
            return ""
        if "--abbrev-ref" in joined:
            return "HEAD"
        raise AssertionError("unexpected command: " + joined)

    git = adapter.collect_git(Path("/repo"), runner=fake_runner)
    assert git["branch_remote_backed"] is False


def test_adapter_stale_remote_sha_is_not_remote_backed(adapter):
    def fake_runner(args):
        joined = " ".join(args)
        if "status" in joined:
            return ""
        if "@{upstream}..HEAD" in joined:
            return "0"
        if "@{upstream}" in joined:
            return "origin/main"
        if "ls-remote" in joined:
            return "remote456\trefs/heads/main"
        if joined.endswith("rev-parse HEAD"):
            return "local123"
        if "--abbrev-ref" in joined:
            return "main"
        raise AssertionError("unexpected command: " + joined)

    git = adapter.collect_git(Path("/repo"), runner=fake_runner)
    assert git["branch_remote_backed"] is False


def test_adapter_unpushed_branch_is_not_remote_backed(adapter):
    def fake_runner(args):
        joined = " ".join(args)
        if "status" in joined:
            return ""
        if "@{upstream}..HEAD" in joined:
            return "2"
        if "@{upstream}" in joined:
            return "origin/main"
        if "--abbrev-ref" in joined:
            return "main"
        raise AssertionError("unexpected command: " + joined)

    git = adapter.collect_git(Path("/repo"), runner=fake_runner)
    assert git["branch_remote_backed"] is False


def test_adapter_collect_runtime_parses_probe_json(adapter):
    payload = {
        "version": "3.3.0",
        "source_path": "/private/tmp/memkraft-3.3/src/memkraft/__init__.py",
        "distribution_version": "3.2.0",
    }

    def fake_runner(args):
        assert args[0] == "/usr/bin/python3"
        return json.dumps(payload)

    assert adapter.collect_runtime("/usr/bin/python3", runner=fake_runner) == payload


def test_adapter_main_returns_nonzero_and_json_on_drift(adapter, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "collect_git", lambda repo, runner=None: {
        "branch": "release/3.3.0",
        "tree_clean": True,
        "branch_remote_backed": False,
    })
    monkeypatch.setattr(adapter, "collect_runtime", lambda python, runner=None: {
        "version": "3.3.0",
        "source_path": "/private/tmp/memkraft-3.3/src/memkraft/__init__.py",
        "distribution_version": "3.2.0",
    })
    monkeypatch.setattr(adapter, "read_source_version", lambda repo: "3.3.0")

    code = adapter.main([
        "--repo", str(tmp_path),
        "--public-version", "3.2.0",
        "--snapshot-public-version", "3.1.0",
        "--snapshot-development-version", "3.2.0",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert code != 0
    assert payload["release_ready"] is False
    assert len(payload["findings"]) == 4


def test_adapter_main_returns_zero_on_healthy_state(adapter, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "collect_git", lambda repo, runner=None: {
        "branch": "release/3.3.0",
        "tree_clean": True,
        "branch_remote_backed": True,
    })
    monkeypatch.setattr(adapter, "collect_runtime", lambda python, runner=None: {
        "version": "3.3.0",
        "source_path": "/Users/dev/work/memkraft/src/memkraft/__init__.py",
        "distribution_version": "3.3.0",
    })
    monkeypatch.setattr(adapter, "read_source_version", lambda repo: "3.3.0")

    code = adapter.main([
        "--repo", str(tmp_path),
        "--public-version", "3.3.0",
        "--snapshot-public-version", "3.3.0",
        "--snapshot-development-version", "3.3.0",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["release_ready"] is True
    assert payload["findings"] == []


def test_adapter_allow_ephemeral_source_drops_only_path_constraint(adapter, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "collect_git", lambda repo, runner=None: {
        "branch": "release/3.3.0",
        "tree_clean": True,
        "branch_remote_backed": True,
    })
    monkeypatch.setattr(adapter, "collect_runtime", lambda python, runner=None: {
        "version": "3.3.0",
        "source_path": "/tmp/memkraft-3.3/src/memkraft/__init__.py",
        "distribution_version": "3.3.0",
    })
    monkeypatch.setattr(adapter, "read_source_version", lambda repo: "3.3.0")

    argv = [
        "--repo", str(tmp_path),
        "--public-version", "3.3.0",
        "--snapshot-public-version", "3.3.0",
        "--snapshot-development-version", "3.3.0",
    ]
    strict = adapter.main(argv)
    strict_payload = json.loads(capsys.readouterr().out)
    assert strict != 0
    assert [f["constraint_id"] for f in strict_payload["findings"]] == ["durable_source_path"]

    relaxed = adapter.main(argv + ["--allow-ephemeral-source"])
    relaxed_payload = json.loads(capsys.readouterr().out)
    assert relaxed == 0
    assert relaxed_payload["findings"] == []


def test_adapter_read_source_version(adapter, tmp_path):
    pkg = tmp_path / "src" / "memkraft"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""doc"""\n\n__version__ = "9.9.9"\n', encoding="utf-8")
    assert adapter.read_source_version(tmp_path) == "9.9.9"


def test_adapter_module_makes_no_network_or_mutating_calls():
    source = (REPO_ROOT / "scripts" / "check_project_state.py").read_text(encoding="utf-8")
    for banned in ("socket", "urllib", "requests", "pip install", "git push", "git commit"):
        assert banned not in source
