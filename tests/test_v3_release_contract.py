"""Executable contract for the MemKraft 3.0 Phase-A compatibility surface."""
from __future__ import annotations

import ast
import json
import re
import warnings
from pathlib import Path

import pytest

from benchmarks.external.sample_adapter import SampleAdapter, run_sample
from benchmarks.gym import gates, scenarios
from benchmarks.gym import run as gym_run
from memkraft import MemKraft

ROOT = Path(__file__).parents[1]
STABLE_VERBS = (
    "append_event", "compile_truth", "current_truth", "sleep", "forget",
    "compile_context", "report_outcome",
)


def test_documented_stable_verbs_exist_and_contract_is_linked():
    text = (ROOT / "docs" / "V3_API.md").read_text(encoding="utf-8")
    for verb in STABLE_VERBS:
        assert hasattr(MemKraft, verb)
        assert f"`{verb}`" in text
    for heading in ("Stable core", "Preview and secondary", "Search modes", "Provenance", "Lifecycle", "Governance boundary"):
        assert heading in text


def test_record_event_is_non_breaking_deprecated_alias(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    with pytest.warns(DeprecationWarning, match="append_event"):
        row = mk.record_event("u", "city", "Seoul", source="test:fixture")
    assert row["subject_id"] == "u"
    assert mk.append_event.__func__ is not mk.record_event.__func__


def test_resolver_roadmap_alias_warns_and_matches_canonical(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    claims = [{"subject": "u", "predicate": "prefers", "object_value": "tea", "confidence": .9, "source_quote": "I prefer tea"}]
    expected = mk.resolver_dry_run(claims)
    with pytest.warns(DeprecationWarning, match="resolver_dry_run"):
        assert mk.resolve_claims(claims) == expected


def test_search_mode_is_canonical_and_old_named_search_warns(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    mk.track("mode-fixture", source="test")
    mk.update("mode-fixture", "needle", source="test")
    canonical = mk.search("needle", mode="v2", top_k=2)
    with pytest.warns(DeprecationWarning, match="search.*mode"):
        legacy = mk.search_v2("needle", top_k=2)
    assert [r["file"] for r in legacy] == [r["file"] for r in canonical]
    with pytest.raises(ValueError, match="mode"):
        mk.search("needle", mode="unknown")


@pytest.mark.parametrize("mode", ["legacy", "v2", "smart", "hybrid"])
def test_canonical_search_modes_never_emit_deprecation_warning(tmp_path: Path, mode: str):
    mk = MemKraft(str(tmp_path))
    mk.track("mode-fixture", source="test")
    mk.update("mode-fixture", "needle", source="test")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mk.search("needle", mode=mode, top_k=2)
    assert not [item for item in caught if issubclass(item.category, DeprecationWarning)]


@pytest.mark.parametrize("name", ["search_v2", "search_smart", "search_hybrid"])
def test_direct_named_search_aliases_emit_exactly_one_warning(tmp_path: Path, name: str):
    mk = MemKraft(str(tmp_path))
    mk.track("mode-fixture", source="test")
    mk.update("mode-fixture", "needle", source="test")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        getattr(mk, name)("needle", top_k=2)
    deprecations = [item for item in caught if issubclass(item.category, DeprecationWarning)]
    assert len(deprecations) == 1
    assert name in str(deprecations[0].message)


def test_lifecycle_replay_fixture_is_deterministic_and_gated():
    first = scenarios.run_scenario("lifecycle_replay", sizes=[1])
    second = scenarios.run_scenario("lifecycle_replay", sizes=[99])
    assert first == second
    result = first["results"][0]
    assert result["recall"] is True
    assert result["tombstone_hidden"] is True
    assert result["tombstone_hidden_current_truth"] is True
    assert result["tombstone_hidden_context"] is True
    assert result["tombstone_hidden_search"] is True
    assert result["no_unsourced_derived_writes"] is True
    assert result["sleep_transaction_stable"] is True
    assert result["outcome_recorded"] is True
    assert result["mean_recall_at_k"] == result["min_recall_at_k"] == 1.0
    assert gates.evaluate_gate(first)["passed"] is True


def _gym_workflow_invocations(text=None) -> list[dict]:
    """Parse every uncommented ``benchmarks/gym/run.py`` shell invocation."""
    if text is None:
        text = (ROOT / ".github" / "workflows" / "gym-gate.yml").read_text(encoding="utf-8")
    uncommented = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    flattened = uncommented.replace("\\\n", " ")
    invocations = []
    for line in flattened.splitlines():
        if "benchmarks/gym/run.py" not in line:
            continue
        match = re.search(r"--scenario[=\s]+([A-Za-z0-9_]+)", line)
        invocations.append(
            {"scenario": match.group(1) if match else None, "gated": "--gate" in line.split()}
        )
    return invocations


def test_gym_gate_workflow_gates_every_invoked_scenario():
    invocations = _gym_workflow_invocations()
    assert invocations, "gym-gate.yml no longer invokes benchmarks/gym/run.py"
    ungated = sorted(str(inv["scenario"]) for inv in invocations if not inv["gated"])
    assert ungated == [], f"CI Gym scenarios invoked without --gate: {ungated}"


def test_gym_workflow_parser_does_not_count_gate_in_shell_comment():
    text = "run: python benchmarks/gym/run.py --scenario claim_extraction # --gate\n"
    assert _gym_workflow_invocations(text) == [{"scenario": "claim_extraction", "gated": False}]


def test_gym_gate_workflow_invokes_every_implemented_ci_scenario():
    invoked = {inv["scenario"] for inv in _gym_workflow_invocations()}
    missing = sorted(set(scenarios.registered_scenarios()) - invoked)
    assert missing == [], f"registered Gym scenarios missing from gym-gate.yml: {missing}"


@pytest.mark.parametrize(
    "scenario",
    scenarios.registered_scenarios(),
)
def test_every_registered_gym_scenario_passes_its_advertised_gate(tmp_path: Path, scenario: str):
    out = tmp_path / f"{scenario}.json"
    assert gym_run.main(["--scenario", scenario, "--sizes", "1", "--gate", "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["scenario"] == scenario
    assert payload["gate"]["passed"] is True


def test_lifecycle_replay_negative_missing_source_is_rejected():
    fixture = json.loads((ROOT / "benchmarks" / "gym" / "fixtures" / "lifecycle_replay.json").read_text())
    with pytest.raises(ValueError, match="source"):
        scenarios.run_lifecycle_fixture({**fixture, "events": [{"subject_id": "u", "key": "bad", "value": "x"}]})


def test_lifecycle_fixture_uses_public_validation_and_governance(monkeypatch):
    fixture = json.loads((ROOT / "benchmarks" / "gym" / "fixtures" / "lifecycle_replay.json").read_text())
    malformed = {**fixture, "events": [{"id": "bad", "subject_id": [], "key": "bad", "value": "x", "source": "fixture"}]}
    with pytest.raises(TypeError, match="subject_id"):
        scenarios.run_lifecycle_fixture(malformed)
    calls = []
    def deny(self, *args, **kwargs):
        calls.append((args, kwargs))
        raise PermissionError("fixture denied by governance")
    monkeypatch.setattr(MemKraft, "append_event", deny)
    with pytest.raises(PermissionError, match="governance"):
        scenarios.run_lifecycle_fixture(fixture)
    assert calls


@pytest.mark.parametrize("broken", ["truth", "sleep", "outcome"])
def test_lifecycle_gate_fails_when_any_core_invariant_is_mutated(monkeypatch, broken):
    fixture = json.loads((ROOT / "benchmarks" / "gym" / "fixtures" / "lifecycle_replay.json").read_text())
    if broken == "truth":
        fixture["expected_truth"] = {"city": "Busan"}
    elif broken == "sleep":
        original = MemKraft.sleep
        calls = {"count": 0}
        def unstable(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            calls["count"] += 1
            return {**result, "transaction_id": result["transaction_id"] + str(calls["count"])}
        monkeypatch.setattr(MemKraft, "sleep", unstable)
    else:
        original = MemKraft.report_outcome
        def wrong_usage(self, *args, **kwargs):
            return {**original(self, *args, **kwargs), "usage_id": "wrong"}
        monkeypatch.setattr(MemKraft, "report_outcome", wrong_usage)
    payload = scenarios.run_lifecycle_fixture(fixture)
    result = payload["results"][0]
    assert result["mean_recall_at_k"] == result["min_recall_at_k"] == 0.0
    assert gates.evaluate_gate(payload)["passed"] is False


def test_unsourced_derived_predicate_requires_real_source_linkage():
    canonical = [{"id": "event-1", "subject_id": "u", "key": "k", "value": "v", "source": "fixture"}]
    assert scenarios._derived_rows_are_sourced(
        [{"subject_id": "u", "key": "k", "value": "v", "source": "fixture"}], canonical, "tx"
    )
    assert not scenarios._derived_rows_are_sourced([{"action": "sleep"}], canonical, "tx")
    assert not scenarios._derived_rows_are_sourced(
        [{"subject_id": "u", "key": "other", "value": "v", "source": "fixture"}], canonical, "tx"
    )


def test_external_adapter_module_has_no_python310_pep604_annotations():
    path = ROOT / "benchmarks" / "external" / "sample_adapter.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    annotations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations.extend(arg.annotation for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs) if arg.annotation)
            if node.args.vararg and node.args.vararg.annotation:
                annotations.append(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation:
                annotations.append(node.args.kwarg.annotation)
            if node.returns:
                annotations.append(node.returns)
    assert not any(isinstance(child, ast.BinOp) and isinstance(child.op, ast.BitOr)
                   for annotation in annotations for child in ast.walk(annotation))


def test_external_sample_adapter_is_offline_deterministic():
    adapter = SampleAdapter()
    assert adapter.load_cases() == adapter.load_cases()
    first = run_sample(adapter)
    second = run_sample(adapter)
    assert first["scenario"] == "external_sample"
    assert first["dataset_version"] == second["dataset_version"]
    assert first["observed"]["accuracy"] == second["observed"]["accuracy"] == 1.0
    assert first["network_required"] is False
    assert first["model_required"] is False
    assert len(first["per_case"]) >= 2


def test_migration_document_covers_213_to_30_and_rollback():
    text = (ROOT / "docs" / "MIGRATIONS.md").read_text(encoding="utf-8")
    for phrase in ("2.13.x → 3.0", "preview", "events.jsonl", "compiled_truth.jsonl", "compile_context", "report_outcome", "rollback"):
        assert phrase in text


def test_alias_emits_one_warning_per_call(tmp_path: Path):
    mk = MemKraft(str(tmp_path))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mk.record_event("u", "k", "v", source="test")
    assert len([w for w in caught if issubclass(w.category, DeprecationWarning)]) == 1
    assert caught[0].filename == __file__
