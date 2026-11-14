"""Recall abstains instead of returning another scheduled job's prompt.

When nothing but quotes of stored cron prompts matches, the last-resort
fallback used to inject them anyway (5 of 20 real Zeon cron prompts got a
recall block made only of another job's instructions).  An empty recall is
the honest answer; agent-preamble notes that carry a fact stay last resort.
"""
import pytest

pytest.importorskip("agent.memory_provider")

from memkraft import MemKraft
from memkraft.hermes_provider import MemKraftMemoryProvider, _is_stored_job_prompt_hit

CRON = ("User: [IMPORTANT: You are running as a scheduled cron job. DELIVERY: Your final "
        "response will be automatically delivered]")


class _Store:
    base_dir = "."

    def __init__(self, hits):
        self.hits = hits

    def search(self, query, top_k=12):
        return self.hits[:top_k]


def _hit(file, snippet, **extra):
    return dict({"file": file, "snippet": snippet, "score": 1.0}, **extra)


def test_only_cron_prompt_hits_recall_nothing():
    p = MemKraftMemoryProvider()
    p._store = _Store([_hit("artifacts/c%d.md" % i, CRON + " run %d" % i) for i in range(3)])
    assert p._recall_hits("q", want=5) == []


def test_demoted_cron_prompt_quote_is_not_a_fallback():
    hit = _hit("artifacts/x.md", "MANDATORY ESCALATION GATE: write a JSON first", _cron_prompt=True)
    assert _is_stored_job_prompt_hit(hit)
    p = MemKraftMemoryProvider()
    p._store = _Store([hit])
    assert p._recall_hits("q", want=5) == []


def test_prefetch_with_only_cron_prompt_text_injects_no_recall(tmp_path):
    base = tmp_path / "memory"
    MemKraft(base_dir=str(base))
    art = base / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / "a.md").write_text(
        "Recorded: **2026-11-04**\n" + CRON + " Audit the quillferret ledger every hour.\n",
        encoding="utf-8",
    )
    p = MemKraftMemoryProvider()
    p._store = MemKraft(base_dir=str(base))
    assert "MemKraft recall:" not in p.prefetch("quillferret ledger audit")


def test_agent_preamble_fact_is_still_last_resort():
    p = MemKraftMemoryProvider()
    p._store = _Store([_hit("live-notes/o.md", "You are Sano. The zirconlark deploy runs on Fridays.")])
    assert len(p._recall_hits("q", want=5)) == 1


def test_cron_run_reply_is_still_recalled():
    hit = _hit("artifacts/s.md", "User: [scheduled job prompt omitted: x | 12 chars]\nAssistant: archived 7 ledgers")
    assert not _is_stored_job_prompt_hit(hit)
    p = MemKraftMemoryProvider()
    p._store = _Store([hit])
    assert len(p._recall_hits("q", want=5)) == 1
