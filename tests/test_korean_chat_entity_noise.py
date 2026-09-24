"""Regression tests for Korean chat-turn entity noise and stub-dominated recall.

Reproduces a live Hermes store where requests such as "메뉴에서 보이게 해줘"
created entity pages named 보이게/해줘/링크도, a bold markdown URL became the
entity "https://...children-of-resonance**", and those empty pages occupied
prefetch slots ahead of real facts.
"""
from memkraft import MemKraft
from memkraft._core_search_helpers import (
    is_korean_predicate_token,
    is_stub_entity_text,
)


def _names(mk, text):
    return {e["name"] for e in mk._detect_regex(text)}


def test_chat_request_predicates_are_not_entities(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    names = _names(mk, "앞으로 남은 검토 리스트들도 메뉴에서 보이게 해줘")
    assert "보이게" not in names
    assert "해줘" not in names
    names = _names(mk, "원래 소설 서브스택 원문 찾아. 다 고쳐서 배포하고 링크도 줘.")
    assert "찾아" not in names


def test_short_korean_names_survive(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    names = _names(mk, "하나와 튜링이 시온 연구실에서 만났다.")
    assert "하나" in names or "하나와" in names
    assert "시온" in names
    for token in ("하나", "시온", "사노", "라온", "소피아"):
        assert not is_korean_predicate_token(token)


def test_markdown_wrapped_url_is_cleaned(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    names = _names(mk, "원문: **https://simon0x.substack.com/p/children-of-resonance**")
    assert "https://simon0x.substack.com/p/children-of-resonance" in names
    assert not any(n.endswith("*") for n in names)


def test_stub_entity_detection():
    stub = (
        "# 보이게\n\n**Tier: recall**\n\n## State\n- **Role:** (enrichment needed)\n\n"
        "## Open Threads\n- [ ] Initial entity — enrichment needed\n\n---\n\n## Timeline\n\n"
        "- **2026-09-24** | Re-detected [Source: hermes:abc#user]\n"
        "- **2026-09-13** | Entity first detected [Source: hermes:abc#assistant]\n"
    )
    assert is_stub_entity_text(stub)
    enriched = stub + "- **2026-09-25** | 원문은 simon0x.substack.com에 있다 [Source: note]\n"
    assert not is_stub_entity_text(enriched)
