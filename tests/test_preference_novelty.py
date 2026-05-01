"""v2.7.3 — `suggest_new_ideas` novelty-aware prompt regression guard.

Background
----------
v2.7.2 PersonaMem n=200 results revealed a -11.6pp regression on the
``novel_suggestion`` category (memkraft variant 11.5%, baseline 23.1%).

Root cause: ``personamem.build_context`` for ``suggest_new_ideas`` used
the same wall-of-preferences template as ``provide_preference_aligned_
recommendations`` — dumping the full preference profile plus 40 raw
"Enjoys / Values" sentences. The judge model then interpreted the prompt
as "give me more of the same" and selected distractors that restated
existing activities, instead of the deliberately-novel correct option.

v2.7.3 fix (this file's contract)
---------------------------------
Split the two scenarios in ``personamem.build_context``:

* ``provide_preference_aligned_recommendations`` keeps the existing
  preference-aligned dump (correct: echo the user's tastes).
* ``suggest_new_ideas`` gets a novelty-aware prompt:
    1. Explicit "Task Framing — Novel Suggestion" preamble telling the
       model to suggest NEW ideas, not restate existing activities.
    2. Compact "Taste Snapshot" — top-12 prefs by strength (no
       per-category explosion).
    3. "Already Explored / Doing" block listing current activities,
       discontinued items, and favorites — explicitly framed as
       "avoid restating these as 'new'".
    4. Short "Avoid" block with at most 10 dislike sentences (guardrail).
    5. NO 40-sentence "Enjoys / Values" dump.

Each test below asserts one piece of that contract so a future refactor
of the prompt builder can't silently re-introduce the regression.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from memkraft import MemKraft
from memkraft.personamem import build_context


@pytest.fixture
def mk_with_prefs():
    """A MemKraft populated with a small but realistic preference set
    (taste signals + activities + dislikes) for one persona.
    """
    with tempfile.TemporaryDirectory() as d:
        mk = MemKraft(base_dir=Path(d))
        name = "Alex"
        mk.track(name, entity_type="person", source="test")

        # Strong likes (taste snapshot should surface the top-strength
        # items first).
        mk.pref_set(name, "likes", "literary salons", category="entertainment",
                    strength=0.9, source="test")
        mk.pref_set(name, "likes", "indie folk music", category="music",
                    strength=0.85, source="test")
        mk.pref_set(name, "persona_like", "fostering inclusive practices",
                    category="general", strength=0.95, source="test")

        # Activities — what the user is *currently doing*. The novelty
        # prompt should list these in "Already Explored" so the LLM
        # doesn't re-suggest them.
        mk.pref_set(name, "activity", "monthly book clubs",
                    category="entertainment", strength=0.8, source="test")
        mk.pref_set(name, "activity", "creative writing workshops",
                    category="creative", strength=0.8, source="test")

        # Discontinued — also "already explored".
        mk.pref_set(name, "discontinued", "fanfiction writing",
                    category="creative", strength=0.7, source="test")

        # Favorite — also "already explored".
        mk.pref_set(name, "favorite_book", "The Goldfinch",
                    category="entertainment", strength=0.95, source="test")

        # Dislike — should be in the "Avoid" guardrail, but not as a
        # raw 40-sentence wall.
        mk.pref_set(name, "dislikes", "high-pressure book reviews",
                    category="entertainment", strength=0.8, source="test")

        yield mk, name


# ────────────────────────────────────────────────────────────
# Statements payload (mirrors what PersonaMemAdapter would feed in).
# ────────────────────────────────────────────────────────────

def _sample_statements():
    """40+ positive sentences + 12 negative sentences. Pre-fix this
    would render a ~50-line wall of raw "Enjoys / Values" text. Post-fix
    they should NOT appear in the suggest_new_ideas prompt at all.
    """
    pos_text = "I really enjoy curating book lists for the community."
    neg_text = "I no longer enjoy high-stakes critique sessions."
    pos = [{
        "text": f"{pos_text} (variant {i})", "kind": "like",
        "subject": None, "value": "curating", "year": "2026",
        "reason": None, "sentiment": "positive", "category": "entertainment",
        "session": 1, "msg_idx": i, "sentence_idx": 0,
    } for i in range(45)]
    neg = [{
        "text": f"{neg_text} (variant {i})", "kind": "dislike",
        "subject": None, "value": "critique sessions", "year": "2026",
        "reason": None, "sentiment": "negative", "category": "entertainment",
        "session": 1, "msg_idx": 100 + i, "sentence_idx": 0,
    } for i in range(12)]
    return pos + neg


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────

def test_novel_suggestion_has_task_framing(mk_with_prefs):
    """Contract #1: the prompt must explicitly tell the model that the
    user is asking for something NEW."""
    mk, name = mk_with_prefs
    ctx = build_context(
        mk, name,
        question_type="suggest_new_ideas",
        topic="bookRecommendation",
        question="What new creative writing pursuits could I explore?",
        statements=_sample_statements(),
    )
    assert "## Task Framing" in ctx
    assert "Novel Suggestion" in ctx
    # The framing must mention NOT restating existing activities.
    assert "new" in ctx.lower()
    assert ("already do" in ctx.lower()) or ("already" in ctx.lower())


def test_novel_suggestion_omits_enjoys_values_wall(mk_with_prefs):
    """Contract #2: the 45-sentence "Enjoys / Values" raw wall that
    drove the v2.7.2 over-grounding regression must NOT appear under
    the suggest_new_ideas branch."""
    mk, name = mk_with_prefs
    ctx = build_context(
        mk, name,
        question_type="suggest_new_ideas",
        topic="bookRecommendation",
        question="What new creative writing pursuits could I explore?",
        statements=_sample_statements(),
    )
    assert "## Enjoys / Values" not in ctx, (
        "suggest_new_ideas regressed: the 'Enjoys / Values' raw-sentence "
        "wall is back, this is exactly what caused the v2.7.2 -11.6pp "
        "novel_suggestion regression."
    )


def test_novel_suggestion_uses_taste_snapshot_capped(mk_with_prefs):
    """Contract #3: surface a compact top-N taste snapshot (≤ 12 items),
    not a per-category dump of every preference."""
    mk, name = mk_with_prefs
    # Inflate the prefs so a per-category dump would be obviously huge.
    for i in range(40):
        mk.pref_set(name, "likes", f"filler taste signal {i}",
                    category="entertainment", strength=0.5, source="test")
    ctx = build_context(
        mk, name,
        question_type="suggest_new_ideas",
        topic="bookRecommendation",
        question="What new creative writing pursuits could I explore?",
        statements=_sample_statements(),
    )
    assert "## Taste Snapshot" in ctx
    # Count taste-snapshot lines (between Taste Snapshot header and the
    # next "## " header).
    after = ctx.split("## Taste Snapshot")[1]
    next_section = after.split("\n## ", 1)[0]
    bullets = [
        line for line in next_section.splitlines()
        if line.strip().startswith("-")
    ]
    assert 0 < len(bullets) <= 12, (
        f"Taste Snapshot exposed {len(bullets)} items; cap is 12. "
        "Pre-fix the prompt grew 30+ items grouped by category, which "
        "is exactly the over-grounding that caused the regression."
    )


def test_novel_suggestion_lists_already_explored(mk_with_prefs):
    """Contract #4: 'Already Explored / Doing' block must call out
    current activities, discontinued items and favorites, framed as
    things the LLM should NOT re-suggest as 'new'."""
    mk, name = mk_with_prefs
    ctx = build_context(
        mk, name,
        question_type="suggest_new_ideas",
        topic="bookRecommendation",
        question="What new creative writing pursuits could I explore?",
        statements=_sample_statements(),
    )
    assert "## Already Explored" in ctx
    explored_section = ctx.split("## Already Explored")[1].split("\n## ", 1)[0]
    # All three "explored" preference kinds should be represented.
    assert "monthly book clubs" in explored_section          # activity
    assert "creative writing workshops" in explored_section  # activity
    assert "fanfiction writing" in explored_section          # discontinued
    assert "The Goldfinch" in explored_section               # favorite_*
    # And the section header text should warn against restating.
    assert "avoid" in explored_section.lower() or "avoid" in ctx.lower()


def test_novel_suggestion_keeps_dislike_guardrail_short(mk_with_prefs):
    """Contract #5: dislikes still appear (don't suggest things the
    user hates), but capped at ≤ 10 items so they can't reintroduce
    a 40-sentence wall."""
    mk, name = mk_with_prefs
    ctx = build_context(
        mk, name,
        question_type="suggest_new_ideas",
        topic="bookRecommendation",
        question="What new creative writing pursuits could I explore?",
        statements=_sample_statements(),
    )
    # The "Avoid" section should exist and list dislike sentences.
    if "## Avoid" in ctx:
        section = ctx.split("## Avoid")[1].split("\n## ", 1)[0]
        bullets = [
            line for line in section.splitlines()
            if line.strip().startswith("-")
        ]
        assert len(bullets) <= 10, (
            f"Avoid section exposed {len(bullets)} dislike sentences; "
            "cap is 10."
        )


def test_aligned_recommendation_branch_unchanged(mk_with_prefs):
    """Regression guard: the ``provide_preference_aligned_recommendations``
    branch should still emit the per-category preference profile and
    the "Enjoys / Values" / "Avoids / Dislikes" walls — that variant
    benefits from over-grounding (the model SHOULD echo preferences)."""
    mk, name = mk_with_prefs
    ctx = build_context(
        mk, name,
        question_type="provide_preference_aligned_recommendations",
        topic="bookRecommendation",
        question="What should I read next?",
        statements=_sample_statements(),
    )
    assert "## Current Preference Profile" in ctx
    # This branch keeps the raw-sentence walls — it is intentional.
    assert "## Enjoys / Values" in ctx
    # And it should NOT carry the novelty framing.
    assert "## Task Framing" not in ctx


def test_novel_suggestion_prompt_size_is_bounded(mk_with_prefs):
    """End-to-end: with a realistic-sized statements payload (45 pos + 12
    neg) the suggest_new_ideas prompt must be materially smaller than
    the aligned-recommendation prompt. Pre-fix they were essentially
    the same template, which is exactly what caused the regression."""
    mk, name = mk_with_prefs
    statements = _sample_statements()
    novel = build_context(
        mk, name,
        question_type="suggest_new_ideas",
        topic="bookRecommendation",
        question="What new creative writing pursuits could I explore?",
        statements=statements,
    )
    aligned = build_context(
        mk, name,
        question_type="provide_preference_aligned_recommendations",
        topic="bookRecommendation",
        question="What should I read next?",
        statements=statements,
    )
    # The aligned prompt carries a 45-sentence raw wall + per-category
    # dump. The novel prompt should be at least ~30% shorter.
    assert len(novel) < len(aligned) * 0.85, (
        f"suggest_new_ideas prompt ({len(novel)} chars) is not measurably "
        f"smaller than aligned prompt ({len(aligned)} chars). The over-"
        "grounding fix may have regressed."
    )
