"""Curated lessons must not fire on one generic shared word.

Regression source: 2026-11-12 replay of 1,369 real requests across five Hermes profiles.
653 curated lesson hits; 483 of them matched on a single trigger word, mostly short
generic Korean words such as 이거/내용/하자 that also appear in other lessons' examples
(e.g. "이거 영어로 번역해줘" pulled the "long builds run in background" rule).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from memkraft import MemKraft


@pytest.fixture
def mk(tmp_path: Path) -> MemKraft:
    m = MemKraft(base_dir=str(tmp_path))
    m.lesson_add("long-build-bg", rule="빌드·배포는 background+notify로 돌린다.",
                 triggers=["빌드", "배포", "이거"], examples=["이거 배포까지 해줘"])
    m.lesson_add("finish-all-parts", rule="여러 부분 요청은 전부 끝낸다.",
                 triggers=["전부", "이거"], examples=["이거 전부 마무리해"])
    m.lesson_add("rights-full", rule="전사 요청은 전문으로 한다.",
                 triggers=["전사", "이거"], examples=["이거 영상 전사해줘"])
    return m


def _ids(mk, q):
    return [h["task_id"] for h in mk.reasoning_anti_patterns(q, top_k=3) if h.get("curated")]


def test_single_generic_word_does_not_fire(mk):
    assert "long-build-bg" not in _ids(mk, "이거 영어로 번역해줘")


def test_specific_trigger_still_fires(mk):
    assert "long-build-bg" in _ids(mk, "프로덕션 빌드 돌려")


def test_own_example_shape_still_fires(mk):
    # one trigger overlap, but shares 2+ tokens with this lesson's own example
    assert "rights-full" in _ids(mk, "이거 영상 정리해줘")


def test_stored_examples_still_recall(mk):
    assert mk.lesson_check()["ok"] is True
