"""Curated lessons keep a pointer to where they were learned (WikiTrace-style provenance).

Regression source: 2026-11-12 review — 0 of 55 hand-written lessons in a live store
could be traced back to the conversation that produced them.
"""
from __future__ import annotations

from pathlib import Path

from memkraft import MemKraft


def test_lesson_source_is_stored_listed_and_injected(tmp_path: Path):
    mk = MemKraft(base_dir=str(tmp_path))
    out = mk.lesson_add("retry-switch", rule="Same approach failed twice -> switch approach.",
                        triggers="업로드,재시도", examples=["덱 재시도 해줘"],
                        source="session 20261029_070116 msg 42")
    assert out["source"] == "session 20261029_070116 msg 42"
    assert mk.lesson_list()[0]["source"] == "session 20261029_070116 msg 42"
    block = mk.reasoning_inject_for_task("덱 업로드 재시도")
    assert "- Work rule `retry-switch` (from \"session 20261029_070116 msg 42\"): " in block
    assert mk.lesson_check()["ok"]


def test_lesson_without_source_renders_unchanged(tmp_path: Path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.lesson_add("retry-switch", rule="Switch approach.", triggers="업로드")
    assert mk.lesson_list()[0]["source"] == ""
    assert '- Work rule `retry-switch`: "Switch approach."' in mk.reasoning_inject_for_task("업로드 해줘")
