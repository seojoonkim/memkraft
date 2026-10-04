"""Curated work lessons + Korean/quoted-context recall (4.3.0).

Regression source: 2026-10-05 Hermes session. Workflow mistakes (retrying the same upload
7 times, no progress reports) were never recalled because ReasoningBank only held
auto-captured tool errors, Korean particles broke whole-token matching, and Telegram
"[Replying to: ...]" quotes pulled lessons for the quoted task instead of the request.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.reasoning_bank import _ko_stem, _recall_tokens, _strip_quoted_context


@pytest.fixture
def mk(tmp_path: Path) -> MemKraft:
    return MemKraft(base_dir=str(tmp_path))


def _auto_failure(mk, tid, title, lesson):
    mk.trajectory_start(tid, title=title, tags=["development-experience", "auto-captured", "failure"])
    mk.trajectory_complete(tid, status="failure", lesson=lesson,
                           pattern_signature=f"{tid}::err",
                           tags=["development-experience", "auto-captured", "failure"])


def test_korean_particles_match_their_stem():
    assert _ko_stem("크기를") == "크기"
    assert _ko_stem("다듬어") == "다듬"
    assert _ko_stem("vooy") == "vooy"
    assert "크기" in _recall_tokens("국기 크기를 동일하게 다듬어")


def test_reply_quote_and_memory_block_are_ignored_for_recall():
    q = ('[Replying to: "Cronjob Response: WHOOP 개인 건강 보고"]\n\n멤크래프트 개선 배포까지 해보자\n\n'
         "<memory-context>\nWHOOP 수면\n</memory-context>")
    cleaned = _strip_quoted_context(q)
    assert "WHOOP" not in cleaned
    assert "멤크래프트" in cleaned


def test_reply_quote_does_not_pull_quoted_task_lessons(mk):
    _auto_failure(mk, "whoop", "WHOOP 개인 건강 보고 수면 회복", "For 'WHOOP', avoid read_file.")
    q = '[Replying to: "WHOOP 개인 건강 보고 수면 회복"]\n\n덱 업로드 다시 해줘'
    assert [h["task_id"] for h in mk.reasoning_anti_patterns(q)] == []


def test_curated_lesson_outranks_auto_captured_detours(mk):
    for i in range(3):
        _auto_failure(mk, f"auto-{i}", "남은거 다 해 배포까지", f"For '남은거 다 해 배포까지', avoid tool{i}.")
    mk.lesson_add("progress-report", rule="Report progress and ETA every 3 minutes.",
                  triggers="남은거,느려,늦어,진행", examples=["남은거 다 해"])
    hits = mk.reasoning_anti_patterns("남은거 다 해", top_k=3)
    assert hits[0]["task_id"] == "progress-report"
    assert hits[0]["curated"] is True


def test_curated_lesson_recalled_through_korean_inflection(mk):
    mk.lesson_add("size-once", rule="Compute widths from hard limits first; set once.",
                  triggers="크기,크게,작게,간격,다듬,레이아웃")
    ids = [h["task_id"] for h in mk.reasoning_anti_patterns("국기 크기를 동일하게 다듬어.")]
    assert ids[:1] == ["size-once"]


def test_injection_renders_curated_lesson_as_work_rule(mk):
    mk.lesson_add("retry-switch", rule="Same approach failed twice -> switch approach.",
                  triggers="업로드,다시,올려,재시도")
    block = mk.reasoning_inject_for_task("덱 다시 올려줘")
    # still quoted: recalled memory is untrusted data even when hand-written
    assert '- Work rule `retry-switch`: "Same approach failed twice -> switch approach."' in block


def test_lesson_add_replaces_and_validates(mk):
    mk.lesson_add("x", rule="old", triggers="a1,b1")
    mk.lesson_add("x", rule="new", triggers="c1")
    (les,) = mk.lesson_list()
    assert les["rule"] == "new" and les["triggers"] == ["c1"]
    with pytest.raises(ValueError):
        mk.lesson_add("y", rule="r", triggers="")
    with pytest.raises(ValueError):
        mk.lesson_add("y", rule=" ", triggers="z1")


def test_lesson_check_reports_examples_that_miss(mk):
    mk.lesson_add("deck", rule="Confirm target deck id before upload.", triggers="업로드,링크",
                  examples=["23p 수정해서 덱에 업로드하고 링크 줘", "WHOOP 수면 보고"])
    rep = mk.lesson_check()
    assert rep["checked"] == 2
    assert [m["example"] for m in rep["missed"]] == ["WHOOP 수면 보고"]
    assert rep["ok"] is False


def test_curated_lessons_do_not_change_auto_capture_signatures(mk):
    _auto_failure(mk, "a", "deploy vercel", "avoid npm run")
    before = mk.reasoning_patterns()
    mk.lesson_add("l", rule="r", triggers="deploy")
    after = [p for p in mk.reasoning_patterns() if not p.get("signature", "").startswith("lesson::")]
    assert after == before


def test_cli_lesson_add_list_check(tmp_path: Path):
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = {"MEMKRAFT_DIR": str(tmp_path), "PYTHONPATH": src, "PATH": "/usr/bin:/bin"}

    def run(*args):
        return subprocess.run([sys.executable, "-m", "memkraft.cli", *args], env=env,
                              capture_output=True, text=True)

    r = run("lesson", "add", "size", "--rule", "Set size once.", "--triggers", "크기,간격",
            "--example", "국기 크기를 동일하게")
    assert r.returncode == 0, r.stderr
    assert "size: Set size once." in run("lesson", "list").stdout
    ok = run("lesson", "check")
    assert ok.returncode == 0 and "1/1" in ok.stdout
    run("lesson", "add", "size", "--rule", "Set size once.", "--triggers", "크기",
        "--example", "WHOOP 보고")
    bad = run("lesson", "check")
    assert bad.returncode == 1 and "MISS size" in bad.stdout
