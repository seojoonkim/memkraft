from __future__ import annotations

from datetime import datetime, timezone

from memkraft import MemKraft, MemoryAdapter


NOW = datetime(2026, 8, 20, 4, 30, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def test_adapter_remember_and_recall_are_transport_neutral(tmp_path):
    adapter = MemoryAdapter(MemKraft(base_dir=str(tmp_path)))

    remembered = adapter.remember("Simon", "Prefers concise Korean replies", source="test")
    recalled = adapter.recall("concise Korean", top_k=5)

    assert remembered["ok"] is True
    assert remembered["operation"] == "remember"
    assert recalled["ok"] is True
    assert recalled["operation"] == "recall"
    assert recalled["results"]
    assert all("file" in item and "score" in item for item in recalled["results"])


def test_adapter_feedback_records_sanitized_experience(tmp_path):
    adapter = MemoryAdapter(MemKraft(base_dir=str(tmp_path)))

    result = adapter.feedback(
        classification="correction",
        task_ref="task:adapter-1",
        outcome_ref="outcome:adapter-1",
        input_snapshot_ref="snapshot:adapter-1",
        artifact_kind="memory_policy",
        replayability="replayable",
        now=NOW,
        evidence_refs=["evidence:adapter-1"],
    )

    assert result["ok"] is True
    assert result["operation"] == "feedback"
    assert result["record"]["classification"] == "correction"
    assert adapter.memory.self_evolving_project()["experience_count"] == 1


def test_adapter_health_returns_stable_envelope(tmp_path):
    adapter = MemoryAdapter(MemKraft(base_dir=str(tmp_path)))

    result = adapter.health()

    assert result["ok"] is True
    assert result["operation"] == "health"
    assert isinstance(result["health"], dict)


def test_adapter_converts_invalid_feedback_to_structured_error(tmp_path):
    adapter = MemoryAdapter(MemKraft(base_dir=str(tmp_path)))

    result = adapter.feedback(
        classification="not-a-classification",
        task_ref="task:bad",
        outcome_ref="outcome:bad",
        input_snapshot_ref="snapshot:bad",
        artifact_kind="memory_policy",
        replayability="replayable",
        now=NOW,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "E_IMPROVEMENT_VALIDATION"
    assert result["error"]["retryable"] is False


def test_adapter_exposes_fake_openclaw_shape(tmp_path):
    adapter = MemoryAdapter(MemKraft(base_dir=str(tmp_path)))

    class FakeOpenClaw:
        def __init__(self, memory):
            self.memory = memory

        def remember(self, **payload):
            return self.memory.remember(**payload)

        def recall(self, **payload):
            return self.memory.recall(**payload)

        def feedback(self, **payload):
            return self.memory.feedback(**payload)

        def health(self):
            return self.memory.health()

    agent = FakeOpenClaw(adapter)
    assert agent.remember(name="Project", info="MemKraft", source="openclaw")["ok"]
    assert agent.recall(query="MemKraft")["ok"]
    assert agent.health()["ok"]
