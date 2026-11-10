"""Auto-captured failure lessons must earn injection (quarantine + contradiction).

Replaying a week of real turns showed every injected "avoid <tool>" line came
from an auto-captured one-off tool error, often for tools that later succeeded.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from memkraft import MemKraft

AUTO = ["development-experience", "auto-captured"]


@pytest.fixture
def mk(tmp_path: Path) -> MemKraft:
    return MemKraft(base_dir=str(tmp_path))


def _auto(mk, tid, title, status, sig, lesson):
    mk.trajectory_start(tid, title=title, tags=AUTO + [status])
    mk.trajectory_complete(tid, status=status, lesson=lesson, pattern_signature=sig, tags=AUTO + [status])


def test_one_off_auto_failure_is_quarantined(mk):
    _auto(mk, "f1", "deploy landing preview", "failure", "web_extract::timeout",
          "Avoid web_extract; it failed with timeout. Task: 'deploy landing preview'.")
    block, meta = mk.reasoning_inject_for_task("deploy landing preview", return_metadata=True)
    assert "Avoid web_extract" not in block
    assert meta["omitted"]["unproven_auto"] == 1


def test_repeated_auto_failure_is_injected(mk):
    for i in range(2):
        _auto(mk, f"f{i}", "deploy landing preview", "failure", "web_extract::timeout",
              "Avoid web_extract; it failed with timeout. Task: 'deploy landing preview'.")
    block = mk.reasoning_inject_for_task("deploy landing preview")
    assert "Avoid web_extract" in block


def test_auto_failure_contradicted_by_later_success_is_dropped(mk):
    for i in range(3):
        _auto(mk, f"f{i}", "run unit tests for parser", "failure", "terminal:python3::tool-error",
              "Avoid terminal:python3; it failed with tool-error. Task: 'run unit tests for parser'.")
    _auto(mk, "s1", "run unit tests for parser", "success",
          "read_file -> terminal:python3::terminal:python3",
          "Reuse read_file -> terminal:python3. Task: 'run unit tests for parser'.")
    block = mk.reasoning_inject_for_task("run unit tests for parser")
    assert "Avoid terminal:python3" not in block
    assert "Reuse read_file" in block


def test_curated_and_manual_failures_are_untouched(mk):
    mk.trajectory_start("manual", title="Vercel deploy missed ready check", tags="deploy,vercel")
    mk.trajectory_complete("manual", status="failure",
                           lesson="Always wait for Vercel ready status before validating deploys")
    block = mk.reasoning_inject_for_task("Vercel deploy ready check")
    assert "Always wait for Vercel ready status" in block
