"""Chat-derived template entity pages must not occupy Hermes recall slots.

Reproduces pages such as entities/게이트웨.md whose timeline rows are clause
fragments copied from Hermes turns ("재시작까지 끝났어") while the page is still an
unenriched template. Real knowledge pages (enriched, or with non-chat sources)
must stay searchable.
"""
from memkraft._core_search_helpers import is_chat_derived_template_entity

TEMPLATE = """# {name}

**Tier: recall**

## State
- **Role:** (enrichment needed)

## Open Threads
- [ ] Initial entity — enrichment needed

---

## Timeline

{rows}
"""


def page(name, rows):
    return TEMPLATE.format(name=name, rows="\n".join(rows))


def test_chat_fragment_template_page_is_filtered():
    text = page("게이트웨", [
        "- **2026-09-24** | 재시작까지 끝났어 [Source: hermes:20260912_232832_08788d28#assistant | Confidence: experimental]",
        "- **2026-09-24** | [CONFLICT] Detected conflict: 'a' vs 'b' [Source: hermes:20260912_232832_08788d28#assistant]",
        "- **2026-09-24** | Re-detected [Source: hermes:20260912_232832_08788d28#user]",
    ])
    assert is_chat_derived_template_entity(text)


def test_page_with_non_chat_source_is_kept():
    text = page("라온", [
        "- **2026-09-24** | 재시작까지 끝났어 [Source: hermes:abc#assistant | Confidence: experimental]",
        "- **2026-07-02** | Resonance Desk 원문 수집 [Source: manual | Confidence: verified]",
    ])
    assert not is_chat_derived_template_entity(text)


def test_enriched_page_is_kept():
    text = "# 시온\n\n## State\n- **Role:** Simon's sister agent\n\n## Timeline\n\n" \
           "- **2026-09-24** | 재시작까지 끝났어 [Source: hermes:abc#assistant]\n"
    assert not is_chat_derived_template_entity(text)


def test_non_hermes_prefix_respected():
    text = page("x", ["- **2026-09-24** | fact [Source: slack:C1#user]"])
    assert not is_chat_derived_template_entity(text)
    assert is_chat_derived_template_entity(text, source_prefix="slack:")
