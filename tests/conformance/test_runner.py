"""Conformance-runner coverage for the generated MKEP/0 corpus."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import runner

CASES = runner.inventory()
EXECUTABLE_L2 = tuple(case for case in CASES
                      if case.metadata["level"] == "L2"
                      and case.metadata["executable"]
                      and "python" in case.metadata["transports"])


def test_inventory_schema_and_plan_minimums():
    assert len(CASES) == 167
    assert len(CASES) >= 100
    assert {case.case_id for case in CASES}.issuperset(runner.NAMED_CASE_IDS)
    assert len(runner.NAMED_CASE_IDS) == 32


def test_inventory_has_exactly_the_documented_transport_gaps():
    gaps = runner.transport_gaps(CASES)
    assert [gap.case_id for gap in gaps] == ["CL-01", "MC-01", "XR-01"]
    assert all(gap.reason for gap in gaps)
    assert [gap.level for gap in gaps] == ["L2", "L2", "L3"]


def test_executable_python_l2_inventory_count_is_pinned():
    assert len(EXECUTABLE_L2) == 116
    assert runner.executable_l2_cases() == EXECUTABLE_L2


@pytest.mark.parametrize("case", EXECUTABLE_L2, ids=lambda case: case.case_id)
def test_python_l2_fixture(case, tmp_path):
    result = runner.run_case(case, tmp_path / case.case_id)
    assert result.case_id == case.case_id
    assert result.responses == len(case.requests)


def test_cli_report_names_gaps_instead_of_counting_them_as_passes(monkeypatch, capsys):
    report = runner.Report(
        inventory_count=167,
        named_count=32,
        executable_l2_count=116,
        executed_count=116,
        skipped_gaps=runner.transport_gaps(CASES),
    )
    monkeypatch.setattr(runner, "run", lambda _root: report)
    assert runner.main([]) == 0
    output = capsys.readouterr().out
    assert "inventory=167 named=32 executable_l2=116 executed=116 gaps=3" in output
    assert "GAP CL-01" in output
    assert "GAP MC-01" in output
    assert "GAP XR-01" in output


def test_runner_parses_as_python_39():
    path = Path(runner.__file__)
    ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 9))
