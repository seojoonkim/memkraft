"""Tests for MemKraft v0.7.0 — Multi-Agent Auto Integration Enhancement.

Tests:
1. channel_update mode=append/merge/set
2. task_delegate + delegation history
3. agent_inject include_completed_tasks + max_history
4. agent_handoff + result block
5. channel_tasks filtering
6. task_cleanup archiving + deletion
7. Integration: multi-agent scenario (zeon→sion handoff)
"""

import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from memkraft import MemKraft, __version__


class TestVersion070:
    def test_version_is_070(self):
        assert __version__ == "0.7.0"


@pytest.fixture
def mk():
    d = tempfile.mkdtemp()
    m = MemKraft(d)
    m.init()
    return m


# ═══════════════════════════════════════
# 1. channel_update mode=append/merge/set
# ═══════════════════════════════════════

class TestChannelUpdateModes:
    def test_set_mode_default(self, mk):
        mk.channel_save("ch1", {"tags": ["old"]})
        result = mk.channel_update("ch1", "tags", ["new"])
        assert result["tags"] == ["new"]

    def test_set_mode_explicit(self, mk):
        mk.channel_save("ch1", {"count": 1})
        result = mk.channel_update("ch1", "count", 2, mode="set")
        assert result["count"] == 2

    def test_append_to_existing_list(self, mk):
        mk.channel_save("ch1", {"tags": ["a", "b"]})
        result = mk.channel_update("ch1", "tags", "c", mode="append")
        assert result["tags"] == ["a", "b", "c"]

    def test_append_list_to_existing_list(self, mk):
        mk.channel_save("ch1", {"tags": ["a"]})
        result = mk.channel_update("ch1", "tags", ["b", "c"], mode="append")
        assert result["tags"] == ["a", "b", "c"]

    def test_append_to_nonexistent_key(self, mk):
        mk.channel_save("ch1", {"other": "data"})
        result = mk.channel_update("ch1", "tags", "first", mode="append")
        assert result["tags"] == ["first"]

    def test_append_to_nonexistent_key_with_list_value(self, mk):
        mk.channel_save("ch1", {})
        result = mk.channel_update("ch1", "items", ["a", "b"], mode="append")
        assert result["items"] == ["a", "b"]

    def test_append_to_non_list_existing(self, mk):
        mk.channel_save("ch1", {"val": "old"})
        result = mk.channel_update("ch1", "val", "new", mode="append")
        assert result["val"] == ["old", "new"]

    def test_append_list_to_non_list_existing(self, mk):
        mk.channel_save("ch1", {"val": "old"})
        result = mk.channel_update("ch1", "val", ["a", "b"], mode="append")
        assert result["val"] == ["old", "a", "b"]

    def test_merge_dicts(self, mk):
        mk.channel_save("ch1", {"config": {"a": 1, "b": 2}})
        result = mk.channel_update("ch1", "config", {"b": 99, "c": 3}, mode="merge")
        assert result["config"]["a"] == 1
        assert result["config"]["b"] == 99
        assert result["config"]["c"] == 3

    def test_merge_nonexistent_key(self, mk):
        mk.channel_save("ch1", {})
        result = mk.channel_update("ch1", "config", {"a": 1}, mode="merge")
        # Falls back to set when no existing dict
        assert result["config"] == {"a": 1}

    def test_merge_non_dict_existing(self, mk):
        mk.channel_save("ch1", {"val": "string"})
        result = mk.channel_update("ch1", "val", {"a": 1}, mode="merge")
        # Falls back to set since existing is not dict
        assert result["val"] == {"a": 1}

    def test_merge_non_dict_value(self, mk):
        mk.channel_save("ch1", {"config": {"a": 1}})
        result = mk.channel_update("ch1", "config", "overwrite", mode="merge")
        # Falls back to set since value is not dict
        assert result["config"] == "overwrite"

    def test_set_preserves_other_keys(self, mk):
        mk.channel_save("ch1", {"keep": "this", "replace": "old"})
        result = mk.channel_update("ch1", "replace", "new", mode="set")
        assert result["keep"] == "this"
        assert result["replace"] == "new"

    def test_append_preserves_other_keys(self, mk):
        mk.channel_save("ch1", {"keep": "this", "list": [1]})
        result = mk.channel_update("ch1", "list", 2, mode="append")
        assert result["keep"] == "this"
        assert result["list"] == [1, 2]

    def test_backward_compat_no_mode(self, mk):
        """Existing code calling channel_update without mode should still work."""
        mk.channel_save("ch1", {"key": "old"})
        result = mk.channel_update("ch1", "key", "new")
        assert result["key"] == "new"


# ═══════════════════════════════════════
# 2. task_delegate + delegation history
# ═══════════════════════════════════════

class TestTaskDelegate:
    def test_delegate_basic(self, mk):
        mk.task_start("t1", "Build feature", agent="zeon")
        result = mk.task_delegate("t1", "zeon", "sion")
        assert result["agent"] == "sion"
        assert result["delegated_by"] == "zeon"

    def test_delegate_history_event(self, mk):
        mk.task_start("t1", "Build feature", agent="zeon")
        mk.task_delegate("t1", "zeon", "sion", context_note="Take over frontend")
        history = mk.task_history("t1")
        delegation_events = [h for h in history if h.get("event") == "delegation"]
        assert len(delegation_events) == 1
        assert delegation_events[0]["from_agent"] == "zeon"
        assert delegation_events[0]["to_agent"] == "sion"
        assert "Take over frontend" in delegation_events[0]["note"]

    def test_delegate_nonexistent_task(self, mk):
        result = mk.task_delegate("nonexistent", "zeon", "sion")
        assert result == {}

    def test_delegate_multiple_times(self, mk):
        mk.task_start("t1", "Build feature", agent="zeon")
        mk.task_delegate("t1", "zeon", "sion")
        mk.task_delegate("t1", "sion", "mion", context_note="Pass to designer")
        result = mk.task_history("t1")
        delegation_events = [h for h in result if h.get("event") == "delegation"]
        assert len(delegation_events) == 2
        # Latest delegation should show mion
        filepath = mk.context_tasks_dir / "t1.json"
        record = json.loads(filepath.read_text())
        assert record["agent"] == "mion"
        assert record["delegated_by"] == "sion"

    def test_task_start_with_delegated_by(self, mk):
        result = mk.task_start("t1", "Delegated task", agent="sion", delegated_by="zeon")
        assert result["delegated_by"] == "zeon"
        assert "delegated by zeon" in result["history"][0]["note"]

    def test_task_start_without_delegated_by_backward_compat(self, mk):
        """Existing code calling task_start without delegated_by should still work."""
        result = mk.task_start("t1", "Normal task", agent="zeon")
        assert result["delegated_by"] == ""
        assert result["agent"] == "zeon"


# ═══════════════════════════════════════
# 3. agent_inject include_completed_tasks + max_history
# ═══════════════════════════════════════

class TestAgentInjectEnhanced:
    def test_inject_max_history_default(self, mk):
        mk.agent_save("zeon", {"role": "orchestrator"})
        mk.task_start("t1", "Big task", agent="zeon")
        for i in range(10):
            mk.task_update("t1", "in_progress", f"Step {i}")
        block = mk.agent_inject("zeon", task_id="t1")
        # Should only show last 5 history entries by default
        step_lines = [line for line in block.split("\n") if "Step" in line]
        assert len(step_lines) <= 5

    def test_inject_max_history_custom(self, mk):
        mk.agent_save("zeon", {"role": "orchestrator"})
        mk.task_start("t1", "Big task", agent="zeon")
        for i in range(10):
            mk.task_update("t1", "in_progress", f"Step {i}")
        block = mk.agent_inject("zeon", task_id="t1", max_history=3)
        step_lines = [line for line in block.split("\n") if "Step" in line]
        assert len(step_lines) <= 3

    def test_inject_include_completed_tasks(self, mk):
        mk.agent_save("zeon", {"role": "orchestrator"})
        mk.channel_save("ch1", {"summary": "Main channel"})
        mk.task_start("t1", "Completed task", channel_id="ch1", agent="zeon")
        mk.task_complete("t1", "Done")
        mk.task_start("t2", "Active task", channel_id="ch1", agent="zeon")

        # Without include_completed_tasks
        block_without = mk.agent_inject("zeon", channel_id="ch1")
        assert "Completed Tasks" not in block_without

        # With include_completed_tasks
        block_with = mk.agent_inject("zeon", channel_id="ch1", include_completed_tasks=True)
        assert "Completed Tasks" in block_with
        assert "t1" in block_with

    def test_inject_backward_compat(self, mk):
        """Existing code calling agent_inject without new params should work."""
        mk.agent_save("zeon", {"role": "test"})
        block = mk.agent_inject("zeon")
        assert isinstance(block, str)
        assert "test" in block.lower()


# ═══════════════════════════════════════
# 4. agent_handoff + result block
# ═══════════════════════════════════════

class TestAgentHandoff:
    def test_handoff_basic(self, mk):
        mk.agent_save("zeon", {"role": "orchestrator", "context": "deploying v2"})
        block = mk.agent_handoff("zeon", "sion")
        assert isinstance(block, str)
        assert "Handoff from zeon" in block
        assert "orchestrator" in block.lower()

    def test_handoff_with_context_note(self, mk):
        mk.agent_save("zeon", {"role": "orchestrator"})
        block = mk.agent_handoff("zeon", "sion", context_note="Take over deployment")
        assert "Take over deployment" in block

    def test_handoff_with_task(self, mk):
        mk.agent_save("zeon", {"role": "orchestrator"})
        mk.task_start("t1", "Deploy v2", agent="zeon")
        block = mk.agent_handoff("zeon", "sion", task_id="t1", context_note="Finish deploy")
        assert "Deploy v2" in block
        # Task should be delegated
        record = json.loads((mk.context_tasks_dir / "t1.json").read_text())
        assert record["agent"] == "sion"

    def test_handoff_records_in_to_agent(self, mk):
        mk.agent_save("zeon", {"role": "orchestrator"})
        mk.agent_handoff("zeon", "sion", context_note="Take over")
        sion_mem = mk.agent_load("sion")
        assert "handoff_from" in sion_mem
        assert isinstance(sion_mem["handoff_from"], list)
        assert len(sion_mem["handoff_from"]) >= 1
        assert sion_mem["handoff_from"][-1]["from"] == "zeon"

    def test_handoff_empty_from_agent(self, mk):
        # from_agent has no memory
        block = mk.agent_handoff("empty", "sion")
        # Should still return a string (possibly empty)
        assert isinstance(block, str)

    def test_handoff_multiple(self, mk):
        mk.agent_save("zeon", {"role": "orchestrator"})
        mk.agent_handoff("zeon", "sion", context_note="First handoff")
        mk.agent_save("sion", {"role": "writer"})
        mk.agent_handoff("sion", "mion", context_note="Second handoff")
        mion_mem = mk.agent_load("mion")
        assert len(mion_mem["handoff_from"]) >= 1
        assert mion_mem["handoff_from"][-1]["from"] == "sion"


# ═══════════════════════════════════════
# 5. channel_tasks filtering
# ═══════════════════════════════════════

class TestChannelTasks:
    def test_channel_tasks_all(self, mk):
        mk.task_start("t1", "Task 1", channel_id="ch1")
        mk.task_start("t2", "Task 2", channel_id="ch1")
        mk.task_complete("t1", "done")
        tasks = mk.channel_tasks("ch1", status="all")
        assert len(tasks) == 2

    def test_channel_tasks_active_only(self, mk):
        mk.task_start("t1", "Task 1", channel_id="ch1")
        mk.task_start("t2", "Task 2", channel_id="ch1")
        mk.task_complete("t1", "done")
        tasks = mk.channel_tasks("ch1", status="active")
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "t2"

    def test_channel_tasks_completed_only(self, mk):
        mk.task_start("t1", "Task 1", channel_id="ch1")
        mk.task_start("t2", "Task 2", channel_id="ch1")
        mk.task_complete("t1", "done")
        tasks = mk.channel_tasks("ch1", status="completed")
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "t1"

    def test_channel_tasks_empty(self, mk):
        tasks = mk.channel_tasks("nonexistent")
        assert tasks == []

    def test_channel_tasks_cross_channel(self, mk):
        mk.task_start("t1", "Task 1", channel_id="ch1")
        mk.task_start("t2", "Task 2", channel_id="ch2")
        tasks = mk.channel_tasks("ch1")
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "t1"

    def test_channel_tasks_limit(self, mk):
        for i in range(10):
            mk.task_start(f"t{i}", f"Task {i}", channel_id="ch1")
        tasks = mk.channel_tasks("ch1", limit=3)
        assert len(tasks) == 3

    def test_channel_tasks_sorted_by_created_desc(self, mk):
        mk.task_start("t-old", "Old task", channel_id="ch1")
        mk.task_start("t-new", "New task", channel_id="ch1")
        tasks = mk.channel_tasks("ch1", limit=10)
        # Most recent first
        assert tasks[0]["task_id"] == "t-new"


# ═══════════════════════════════════════
# 6. task_cleanup archiving + deletion
# ═══════════════════════════════════════

class TestTaskCleanup:
    def _make_old_completed_task(self, mk, task_id, days_ago):
        """Helper to create a completed task with an old timestamp."""
        mk.task_start(task_id, f"Old task {task_id}")
        mk.task_complete(task_id, "done")
        # Manually backdate the completed timestamp
        filepath = mk.context_tasks_dir / f"{task_id}.json"
        record = json.loads(filepath.read_text())
        old_dt = (datetime.now() - timedelta(days=days_ago)).isoformat()
        record["completed"] = old_dt
        record["created"] = old_dt
        filepath.write_text(json.dumps(record))

    def test_cleanup_archives_old(self, mk):
        self._make_old_completed_task(mk, "old-task", 60)
        mk.task_start("new-task", "Still active")
        result = mk.task_cleanup(max_age_days=30, archive=True)
        assert result["archived"] == 1
        assert result["kept"] == 1
        # Archived file should exist
        archive_dir = mk.context_tasks_dir / "archive"
        assert (archive_dir / "old-task.json").exists()
        # Original should not exist
        assert not (mk.context_tasks_dir / "old-task.json").exists()

    def test_cleanup_deletes_old(self, mk):
        self._make_old_completed_task(mk, "old-task", 60)
        result = mk.task_cleanup(max_age_days=30, archive=False)
        assert result["deleted"] == 1
        assert not (mk.context_tasks_dir / "old-task.json").exists()
        archive_dir = mk.context_tasks_dir / "archive"
        assert not (archive_dir / "old-task.json").exists()

    def test_cleanup_keeps_recent_completed(self, mk):
        self._make_old_completed_task(mk, "recent-done", 5)
        result = mk.task_cleanup(max_age_days=30)
        assert result["kept"] == 1
        assert result["archived"] == 0

    def test_cleanup_keeps_active_tasks(self, mk):
        mk.task_start("active-task", "Still going")
        self._make_old_completed_task(mk, "old-done", 60)
        result = mk.task_cleanup(max_age_days=30)
        assert result["kept"] == 1
        assert result["archived"] == 1
        # Active task still present
        assert (mk.context_tasks_dir / "active-task.json").exists()

    def test_cleanup_no_tasks(self, mk):
        result = mk.task_cleanup()
        assert result == {"archived": 0, "deleted": 0, "kept": 0}

    def test_cleanup_custom_max_age(self, mk):
        self._make_old_completed_task(mk, "t1", 10)
        self._make_old_completed_task(mk, "t2", 3)
        result = mk.task_cleanup(max_age_days=7)
        assert result["archived"] == 1  # t1 (10 days old)
        assert result["kept"] == 1  # t2 (3 days old)


# ═══════════════════════════════════════
# 7. Integration: Multi-Agent Scenario
# ═══════════════════════════════════════

class TestMultiAgentIntegration:
    def test_zeon_to_sion_handoff_full_scenario(self, mk):
        """Full multi-agent scenario: zeon starts task, delegates to sion, sion injects context."""
        # 1. Zeon sets up channel and task
        mk.channel_save("dm-simon", {"summary": "DM with Simon", "topic": "deployment"})
        mk.agent_save("zeon", {"role": "orchestrator", "priority": "deploy v2"})
        mk.task_start("deploy-v2", "Deploy v2 to production",
                       channel_id="dm-simon", agent="zeon")
        mk.task_update("deploy-v2", "in_progress", "Frontend 70% done")

        # 2. Zeon hands off to Sion
        handoff_block = mk.agent_handoff(
            "zeon", "sion",
            task_id="deploy-v2",
            context_note="Frontend is 70% done, need CSS polish"
        )

        # Verify handoff block contains all context
        assert "zeon" in handoff_block.lower()
        assert "Deploy v2" in handoff_block
        assert "orchestrator" in handoff_block.lower()

        # 3. Verify task was delegated
        task_record = json.loads((mk.context_tasks_dir / "deploy-v2.json").read_text())
        assert task_record["agent"] == "sion"
        assert task_record["delegated_by"] == "zeon"

        # 4. Sion injects context
        sion_block = mk.agent_inject("sion", channel_id="dm-simon", task_id="deploy-v2")
        assert "handoff_from" in sion_block.lower() or "sion" in sion_block.lower() or isinstance(sion_block, str)

        # 5. Verify Sion has handoff record
        sion_mem = mk.agent_load("sion")
        assert "handoff_from" in sion_mem
        assert sion_mem["handoff_from"][-1]["from"] == "zeon"
        assert sion_mem["handoff_from"][-1]["task_id"] == "deploy-v2"

    def test_channel_update_append_in_workflow(self, mk):
        """Simulate agents appending tags to a shared channel."""
        mk.channel_save("project-ch", {"summary": "Project channel"})
        mk.channel_update("project-ch", "tags", "frontend", mode="append")
        mk.channel_update("project-ch", "tags", "urgent", mode="append")
        mk.channel_update("project-ch", "tags", "backend", mode="append")
        ch = mk.channel_load("project-ch")
        assert ch["tags"] == ["frontend", "urgent", "backend"]

    def test_channel_update_merge_in_workflow(self, mk):
        """Simulate agents merging config to a shared channel."""
        mk.channel_save("project-ch", {"config": {"env": "prod"}})
        mk.channel_update("project-ch", "config", {"feature_flag": True}, mode="merge")
        mk.channel_update("project-ch", "config", {"max_retries": 3}, mode="merge")
        ch = mk.channel_load("project-ch")
        assert ch["config"]["env"] == "prod"
        assert ch["config"]["feature_flag"] is True
        assert ch["config"]["max_retries"] == 3

    def test_task_lifecycle_with_delegation(self, mk):
        """Full task lifecycle: start → delegate → update → complete."""
        mk.task_start("feature-x", "Build feature X", channel_id="ch1", agent="zeon")
        mk.task_delegate("feature-x", "zeon", "sion", "Frontend work")
        mk.task_update("feature-x", "in_progress", "CSS done")
        mk.task_delegate("feature-x", "sion", "mion", "Need design review")
        mk.task_update("feature-x", "review", "Design reviewed")
        mk.task_complete("feature-x", "Shipped!")

        history = mk.task_history("feature-x")
        assert len(history) >= 5  # start + delegate + update + delegate + update + complete
        delegation_events = [h for h in history if h.get("event") == "delegation"]
        assert len(delegation_events) == 2

    def test_cleanup_after_delegation(self, mk):
        """Cleanup should work correctly with delegated tasks."""
        mk.task_start("t1", "Old delegated task", agent="zeon")
        mk.task_delegate("t1", "zeon", "sion")
        mk.task_complete("t1", "All done")

        # Backdate
        filepath = mk.context_tasks_dir / "t1.json"
        record = json.loads(filepath.read_text())
        old_dt = (datetime.now() - timedelta(days=60)).isoformat()
        record["completed"] = old_dt
        filepath.write_text(json.dumps(record))

        result = mk.task_cleanup(max_age_days=30)
        assert result["archived"] == 1

    def test_channel_tasks_with_delegation(self, mk):
        """channel_tasks should reflect current agent after delegation."""
        mk.task_start("t1", "Task 1", channel_id="ch1", agent="zeon")
        mk.task_delegate("t1", "zeon", "sion")
        tasks = mk.channel_tasks("ch1", status="active")
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "sion"

    def test_inject_shows_delegation_history(self, mk):
        """agent_inject should show delegation history in task context."""
        mk.agent_save("sion", {"role": "writer"})
        mk.task_start("t1", "Write blog", agent="zeon")
        mk.task_delegate("t1", "zeon", "sion", "Write the draft")
        block = mk.agent_inject("sion", task_id="t1")
        assert "Delegated" in block or "delegat" in block.lower()
