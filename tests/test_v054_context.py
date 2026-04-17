"""Tests for MemKraft v0.5.4 — Channel Context, Task Continuity, Agent Working Memory."""

import os
import tempfile

import pytest

from memkraft import MemKraft, __version__


class TestVersion:
    def test_version_is_054(self):
        assert __version__ == "0.8.2"


@pytest.fixture
def mk():
    d = tempfile.mkdtemp()
    m = MemKraft(d)
    m.init()
    return m


# ═══════════════════════════════════════
# Channel Context Memory
# ═══════════════════════════════════════

class TestChannelContext:
    def test_channel_save_and_load(self, mk):
        mk.channel_save("telegram-46291309", {"summary": "DM with Simon", "tone": "casual"})
        ch = mk.channel_load("telegram-46291309")
        assert ch["summary"] == "DM with Simon"
        assert ch["tone"] == "casual"

    def test_channel_load_nonexistent(self, mk):
        ch = mk.channel_load("nonexistent-channel")
        assert ch == {}

    def test_channel_update(self, mk):
        mk.channel_save("ch1", {"summary": "test"})
        mk.channel_update("ch1", "mood", "productive")
        ch = mk.channel_load("ch1")
        assert ch["mood"] == "productive"
        assert ch["summary"] == "test"  # original preserved

    def test_channel_update_overwrites_key(self, mk):
        mk.channel_save("ch1", {"summary": "old"})
        mk.channel_update("ch1", "summary", "new")
        ch = mk.channel_load("ch1")
        assert ch["summary"] == "new"

    def test_channel_save_overwrites_all(self, mk):
        mk.channel_save("ch1", {"a": 1, "b": 2})
        mk.channel_save("ch1", {"c": 3})
        ch = mk.channel_load("ch1")
        assert "c" in ch
        # old keys may or may not persist depending on implementation
        # but the new data must be present

    def test_channel_persistence(self, mk):
        mk.channel_save("persist-test", {"data": "survives"})
        # Reload from same path
        mk2 = MemKraft(mk.base_dir)
        ch = mk2.channel_load("persist-test")
        assert ch["data"] == "survives"

    def test_channel_special_chars_in_id(self, mk):
        mk.channel_save("telegram--5173688868", {"summary": "group chat"})
        ch = mk.channel_load("telegram--5173688868")
        assert ch["summary"] == "group chat"

    def test_channel_update_nonexistent_creates(self, mk):
        mk.channel_update("new-ch", "key", "value")
        ch = mk.channel_load("new-ch")
        assert ch["key"] == "value"


# ═══════════════════════════════════════
# Task Continuity Register
# ═══════════════════════════════════════

class TestTaskContinuity:
    def test_task_start(self, mk):
        result = mk.task_start("task-1", "Build feature X")
        assert result is not None

    def test_task_history(self, mk):
        mk.task_start("task-1", "Build feature X")
        h = mk.task_history("task-1")
        assert isinstance(h, list)
        assert len(h) >= 1
        assert h[0]["status"] == "active"

    def test_task_update(self, mk):
        mk.task_start("task-1", "Build feature X")
        mk.task_update("task-1", "in_progress", "50% done")
        h = mk.task_history("task-1")
        assert any(e["status"] == "in_progress" for e in h)
        assert any("50% done" in e["note"] for e in h)

    def test_task_complete(self, mk):
        mk.task_start("task-1", "Build feature X")
        mk.task_complete("task-1", "All done")
        h = mk.task_history("task-1")
        assert h[-1]["status"] == "completed"

    def test_task_list_active(self, mk):
        mk.task_start("t1", "Task 1")
        mk.task_start("t2", "Task 2")
        mk.task_complete("t1", "done")
        active = mk.task_list(status="active")
        assert len(active) == 1
        assert active[0]["task_id"] == "t2"

    def test_task_list_completed(self, mk):
        mk.task_start("t1", "Task 1")
        mk.task_complete("t1", "done")
        completed = mk.task_list(status="completed")
        assert len(completed) == 1

    def test_task_list_all(self, mk):
        mk.task_start("t1", "Task 1")
        mk.task_start("t2", "Task 2")
        mk.task_complete("t1", "done")
        all_tasks = mk.task_list(status="all")
        assert len(all_tasks) >= 2

    def test_task_with_channel_and_agent(self, mk):
        mk.task_start("t1", "Build X", channel_id="ch1", agent="zeon")
        h = mk.task_history("t1")
        # Should store metadata about channel and agent
        assert len(h) >= 1

    def test_task_history_nonexistent(self, mk):
        h = mk.task_history("nonexistent")
        assert h == [] or h == {}

    def test_task_persistence(self, mk):
        mk.task_start("persist-task", "Test persistence")
        mk.task_update("persist-task", "in_progress", "working")
        mk2 = MemKraft(mk.base_dir)
        h = mk2.task_history("persist-task")
        assert len(h) >= 2


# ═══════════════════════════════════════
# Agent Working Memory
# ═══════════════════════════════════════

class TestAgentWorkingMemory:
    def test_agent_save_and_load(self, mk):
        mk.agent_save("zeon", {"key_context": "Orchestrator", "active_tasks": ["t1"]})
        ag = mk.agent_load("zeon")
        assert ag["key_context"] == "Orchestrator"

    def test_agent_load_nonexistent(self, mk):
        ag = mk.agent_load("nonexistent")
        assert ag == {}

    def test_agent_overwrite(self, mk):
        mk.agent_save("zeon", {"v": 1})
        mk.agent_save("zeon", {"v": 2})
        ag = mk.agent_load("zeon")
        assert ag["v"] == 2

    def test_agent_persistence(self, mk):
        mk.agent_save("sion", {"role": "writer"})
        mk2 = MemKraft(mk.base_dir)
        ag = mk2.agent_load("sion")
        assert ag["role"] == "writer"


# ═══════════════════════════════════════
# Agent Inject (Integration)
# ═══════════════════════════════════════

class TestAgentInject:
    def test_inject_all_three(self, mk):
        mk.channel_save("ch1", {"summary": "DM context"})
        mk.task_start("t1", "Feature X", channel_id="ch1", agent="zeon")
        mk.task_update("t1", "in_progress", "halfway")
        mk.agent_save("zeon", {"key_context": "System orchestrator"})

        block = mk.agent_inject("zeon", channel_id="ch1", task_id="t1")
        assert isinstance(block, str)
        assert len(block) > 0
        # Should contain all three contexts
        assert "orchestrator" in block.lower() or "Agent" in block
        assert "DM context" in block or "channel" in block.lower()
        assert "Feature X" in block or "task" in block.lower()

    def test_inject_agent_only(self, mk):
        mk.agent_save("zeon", {"role": "orchestrator"})
        block = mk.agent_inject("zeon")
        assert isinstance(block, str)
        assert "orchestrator" in block.lower()

    def test_inject_no_data(self, mk):
        block = mk.agent_inject("empty-agent")
        assert isinstance(block, str)
        # Should return something (even if minimal) rather than error

    def test_inject_with_channel_no_task(self, mk):
        mk.agent_save("zeon", {"role": "test"})
        mk.channel_save("ch1", {"summary": "Hello"})
        block = mk.agent_inject("zeon", channel_id="ch1")
        assert "Hello" in block

    def test_inject_with_task_no_channel(self, mk):
        mk.agent_save("zeon", {"role": "test"})
        mk.task_start("t1", "Build Y")
        block = mk.agent_inject("zeon", task_id="t1")
        assert "Build Y" in block

    def test_inject_format_is_markdown(self, mk):
        mk.agent_save("zeon", {"role": "test"})
        mk.channel_save("ch1", {"summary": "ctx"})
        mk.task_start("t1", "job")
        block = mk.agent_inject("zeon", channel_id="ch1", task_id="t1")
        # Should use markdown headers
        assert "##" in block
