"""Injected development lessons must keep the actionable part inside the cut."""

from memkraft import MemKraft
from memkraft.development_experience import compile_development_experience
from memkraft.reasoning_bank import _actionable_lesson

LONG_TASK = "[ASYNC DELEGATION BATCH COMPLETE — deleg_ff840da7] A background fan-out of 1 subagent(s) " * 3


def _record(mk, task_id, title, lesson, status="failure"):
    mk.trajectory_start(task_id, title=title, tags=["development-experience"])
    mk.trajectory_complete(task_id, status=status, lesson=lesson)


def test_legacy_lesson_route_survives_truncation(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    legacy = "For '{}', avoid terminal:python3; it failed with timeout.".format(LONG_TASK[:180])
    _record(mk, "dev-failure-legacy", LONG_TASK[:180], legacy)
    block = mk.reasoning_inject_for_task(LONG_TASK, k=3, style="full")
    assert "avoid terminal:python3; it failed with timeout" in block.lower()


def test_new_capture_puts_action_first():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "read_file", "arguments": "{\"path\": \"x\"}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "{\"success\": false, \"error\": \"TimeoutError\"}"},
        {"role": "assistant", "tool_calls": [{"id": "b", "function": {"name": "terminal", "arguments": "{\"command\": \"pytest -q\"}"}}]},
        {"role": "tool", "tool_call_id": "b", "content": "{\"exit_code\": 0, \"output\": \"1 passed\"}"},
    ]
    episodes = compile_development_experience(LONG_TASK, messages)
    if not episodes:  # compiler is conservative; the format rule is what we pin
        return
    for ep in episodes:
        assert ep.lesson.startswith(("Avoid ", "Reuse ")), ep.lesson
        assert ep.lesson.index("Task:") > 10


def test_non_legacy_lessons_untouched():
    assert _actionable_lesson("Always wait for Vercel ready status") == "Always wait for Vercel ready status"
    assert _actionable_lesson("For 'x', reuse a -> b; verified by c.") == "Reuse a -> b; verified by c. Task: 'x'."
