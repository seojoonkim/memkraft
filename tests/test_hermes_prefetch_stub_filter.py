"""Prefetch must surface remembered facts ahead of empty auto-entity pages."""
import pytest

pytest.importorskip("agent.memory_provider")

from memkraft import MemKraft
from memkraft.hermes_provider import MemKraftMemoryProvider


def test_prefetch_prefers_facts_over_stub_entities(tmp_path):
    base = tmp_path / "memory"
    mk = MemKraft(base_dir=str(base))
    mk.init() if hasattr(mk, "init") else None
    ent = base / "entities"
    ent.mkdir(parents=True, exist_ok=True)
    stub_tail = (
        "\n\n**Tier: recall**\n\n## State\n- **Role:** (enrichment needed)\n\n"
        "## Open Threads\n- [ ] Initial entity — enrichment needed\n\n---\n\n## Timeline\n\n"
        "- **2026-09-24** | Re-detected [Source: hermes:s#user]\n"
    )
    for word in ("서브스택", "원문", "소설"):
        (ent / f"{word}.md").write_text(f"# {word}{stub_tail}", encoding="utf-8")
    notes = base / "live-notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "fact.md").write_text(
        "## Completed turn\n\n소설 원문 서브스택 주소는 "
        "https://simon0x.substack.com/p/children-of-resonance 이다.\n",
        encoding="utf-8",
    )
    provider = MemKraftMemoryProvider()
    provider._store = MemKraft(base_dir=str(base))
    out = provider.prefetch("소설 서브스택 원문")
    assert "simon0x.substack.com" in out
    assert "entities/서브스택.md" not in out
    assert "entities/원문.md" not in out
