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


def test_prefetch_drops_chat_fragment_template_pages(tmp_path):
    base = tmp_path / "memory"
    MemKraft(base_dir=str(base))
    ent = base / "entities"
    ent.mkdir(parents=True, exist_ok=True)
    (ent / "게이트웨.md").write_text(
        "# 게이트웨\n\n**Tier: recall**\n\n## State\n- **Role:** (enrichment needed)\n\n"
        "## Open Threads\n- [ ] Initial entity — enrichment needed\n\n---\n\n## Timeline\n\n"
        "- **2026-09-24** | 게이트웨 재시작까지 끝났어 [Source: hermes:s#assistant | Confidence: experimental]\n",
        encoding="utf-8",
    )
    notes = base / "live-notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "fact.md").write_text(
        "## Completed turn\n\n게이트웨 재시작 절차: gateway_restart.py request 후 status 가 DONE 일 때만 완료.\n",
        encoding="utf-8",
    )
    provider = MemKraftMemoryProvider()
    provider._store = MemKraft(base_dir=str(base))
    out = provider.prefetch("게이트웨 재시작")
    assert "gateway_restart.py" in out
    if "entities/게이트웨.md" in out:
        assert out.index("gateway_restart.py") < out.index("entities/게이트웨.md")


def test_filter_prefers_notes_over_chat_template_pages(tmp_path):
    base = tmp_path / "memory"
    ent = base / "entities"
    ent.mkdir(parents=True, exist_ok=True)
    (ent / "기록.md").write_text(
        "# 기록\n\n## State\n- **Role:** (enrichment needed)\n\n---\n\n## Timeline\n\n"
        "- **2026-09-24** | 있을 때만 DONE을 찍어 [Source: hermes:s#assistant | Confidence: experimental]\n",
        encoding="utf-8",
    )
    (ent / "빈.md").write_text(
        "# 빈\n\n## State\n- **Role:** (enrichment needed)\n\n---\n\n## Timeline\n\n"
        "- **2026-09-24** | Re-detected [Source: hermes:s#user]\n",
        encoding="utf-8",
    )
    provider = MemKraftMemoryProvider()
    provider._store = MemKraft(base_dir=str(base))
    hits = [{"file": "entities/기록.md"}, {"file": "entities/빈.md"}, {"file": "live-notes/a.md"}]
    assert [h["file"] for h in provider._filter_stub_hits(hits)] == ["live-notes/a.md"]
    # With no other hit, the chat-derived page is still offered rather than nothing.
    assert [h["file"] for h in provider._filter_stub_hits(hits[:2])] == ["entities/기록.md"]
