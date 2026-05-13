# Compound-Brain — Absorption Strategy

**Author:** Zeon (Opus 4.7 sub-agent)
**Date:** 2026-05-14
**Status:** Strategy doc. Some items already shipped in v2.9.2 (today). Rest awaiting Simon approval per priority.

---

## 0. Terminology — "compound-brain" disambiguated

The term has appeared in three different contexts in Simon's notes; reading them together clarifies the intent:

1. **External GitHub repo `garrytan/gbrain`** (v0.12.3, installed at `~/minions-pilot/gbrain/`) — Garry Tan's personal AI brain. 26 skills, Compiled Truth + Timeline + Hybrid Search + self-wiring KG. MemKraft already absorbed the **foundation pattern** (entity structure = compiled truth + timeline). Per `IMPROVEMENT_IDEAS.md`: *"Already adopted. Foundation of MemKraft's entity structure. Absorbable ideas: None remaining — fully absorbed."* So gbrain itself is not the absorption target anymore.

2. **`~/сlawd/compound/` directory** — operational meta-layer of the 5남매 system (learnings/, subconscious/, work-log.md). Completely separate from MemKraft. Not the target either.

3. **"Compound-brain" as concept** — the *active sense* in recent curation: combining multiple memory subsystems into one substrate. Per `compound/subconscious/curation-queue.md` (2026-05-14):
   - ScriptMem (P1, ✅ candidate)
   - MaTTS (P2, ✅ candidate)
   - Memanto (P1, ✅ candidate)

**Working definition:** *compound-brain = MemKraft v2.9 + ReasoningBank-style trajectory memory + Memanto-style typed facts + ScriptMem-style structured extraction.*

Two of those three patterns (ReasoningBank wrappers + Memanto typed search + StructMem extraction) **shipped today in v2.9.2** — the absorption is actively happening. This doc covers what's done, what's next, and what's risky.

---

## 1. Status Matrix

| Component | Pattern | Status | Where |
|---|---|---|---|
| **ReasoningBank** | trajectory recording, episode recall | ✅ shipped in v2.7.1 (ReasoningBankMixin) | `src/memkraft/reasoning_bank.py` |
| **ReasoningBank convenience API** | one-call `log_reasoning` / `get_similar_reasoning` / `reasoning_stats` | ✅ shipped in **v2.9.2** (today) | `src/memkraft/reasoning_bank.py` |
| **MaTTS-pattern (multi-trajectory)** | parallel rollouts + selection | ⏳ partial — v2.9.2 ReasoningBank ergonomics are step 1; *true* MaTTS rollouts not implemented | future patch |
| **Memanto typed search** | entity-type + fact-key filter on search | ✅ shipped in **v2.9.2** (today) | `src/memkraft/search.py` (`search_typed`) |
| **Memanto auto-resolve** | conflict policy resolution | ❌ not implemented (only conflict *detection* via `mk_pref_conflicts`) | next patch (P1) |
| **StructMem regex extraction** | dates/urls/emails/money/versions/percentages/phones | ✅ shipped in **v2.9.2** (today) | `src/memkraft/struct_mem.py` |
| **ScriptMem benchmark** | dependency-chain QA eval | ❌ not implemented | needs design + adapter (P2) |
| **GBrain self-wiring KG** | typed link extraction on write | ⏳ partial — MemKraft has `[[wiki-link]]` index but no typed predicates (`works_at` / `attended` / etc.) | future feature (P2) |
| **GBrain backlink-boosted ranking** | hybrid search w/ backlink boost | ⏳ MemKraft has hybrid BM25 + fuzzy; backlink boost not implemented | future patch (P2) |
| **GBrain citation-fixer** | nightly cron to repair `[Source: ...]` citations | ❌ not implemented | future skill (P3) |
| **GBrain ingest skills (idea/media/meeting)** | structured ingest pipelines | ❌ not implemented | likely belongs in 5남매 layer, not MemKraft core |

---

## 2. v2.9.2 — What Just Shipped (today, 2026-05-14)

Already merged to main, tagged v2.9.2, published to PyPI:

```python
from memkraft import MemKraft
mk = MemKraft(base_dir="...")

# 1. ReasoningBank ergonomics (was: 3-call trajectory_start/log/complete)
mk.log_reasoning(
    task="batch-merge MemKraft to main",
    outcome="success",
    steps=["test pass", "ff-merge", "tag", "push"],
    tags="release",
)
hits = mk.get_similar_reasoning("how to release MemKraft", top_k=3)
stats = mk.reasoning_stats()
# {total, success, failure, partial, in_progress, success_rate, top_failure_patterns}

# 2. Memanto-style typed search
results = mk.search_typed("CEO", entity_type="person", fact_key="role", top_k=5)
# pre-fetches top_k*5 from search_v2, then post-filters by **Type:** + fact_list key

# 3. StructMem extraction
out = mk.extract_structured(
    "Email me at sj@example.com about the $50k Q2 plan. https://memkraft.io v2.9.2 (95.3%)",
    entity_hint="Simon",  # optional, with auto_save=True writes via fact_add
    auto_save=False,
)
# {dates, urls, emails, money, percentages, versions, phones, saved}
```

**Tests:** 1332 passed, 3 skipped. No external deps. Additive only.

**Carry-overs closed by v2.9.2 release:**
- carry-over #3 ("v2.8 main merge") → **closed** (main is now at v2.9.2)
- carry-over #4 ("corpus_index 캐시 push") → **closed** (v2.9.1 evolution absorbed it)
- carry-over MemKraft v2.9.x publish → **closed** (PyPI shows 2.9.2 live)

---

## 3. Next Absorption Wave — Priorities

### P0 — LME 38pp ablation iteration
Not strictly compound-brain, but it gates everything below. Per `docs/LME_38PP_ABLATION_2026-05-14.md`:
- Gap concentrated in `single-session-preference` (0/3) + `multi-session` (3-7/13)
- Tier 1 fixes (A1 preference extractor + A2 multi-session reranker + A3 category-aware retrieval) expected to lift LME-S 56% → 74-76% for **~$15 + 13h**.

### P1 — Memanto auto-resolve (1 day code, $0 eval)
- MemKraft has `mk_pref_conflicts` for *detection* only. No resolution policy.
- Add `mk.resolve_conflict(entity, key, policy="recency"|"majority"|"highest_confidence")`.
- Default = `recency` (simplest, most predictable).
- API stable, easy to ship as v2.10.0 minor.
- **Risk:** low — purely additive.

### P1 — MaTTS true rollouts (2-3 days code, $20 eval)
- Current v2.9.2 ReasoningBank is *single-trajectory* recall. MaTTS = *multi-trajectory tree search*.
- Concept: at task start, retrieve top-N past trajectories, run M parallel solution paths, score outcomes, write back the winner.
- Implementation: extend `log_reasoning` to accept `parent_episode_id` (tree edge), add `reasoning_tree(task)` that returns the explored tree.
- **Risk:** medium — design surface is large. Recommend a *spec document* first, then code.

### P2 — ScriptMem benchmark adapter (2-3 days code, $30 eval)
- Per `compound/subconscious/curation-queue.md`: P1 candidate, D+14 status.
- Implementation: dataset fetch + `benchmarks/scriptmem/harness.py` reusing LME harness pattern + result report.
- **Risk:** publish only if MemKraft scores competitively. If not, treat as internal-only.

### P2 — GBrain backlink-boosted ranking (1 day code, $5 eval)
- MemKraft has `[[wiki-link]]` index. Add a ranking term: `score += λ * backlink_count(doc)`.
- A/B against current search on existing LME data.
- **Risk:** low. Already validated pattern in gbrain (recall@5 83% → 95%).

### P3 — GBrain typed predicates (1-2 weeks)
- Extract typed predicates (`works_at`, `invested_in`, `attended`) on every page write, store as edges.
- Requires schema choice + edge index format + breaking-or-additive API question.
- **Risk:** high — invasive. Don't ship without design review.

### P3 — Citation auto-repair cron
- GBrain ships this as a nightly skill. MemKraft has `originals/` directory and `[Source: ...]` convention but no auto-repair.
- Skill belongs in the **5남매 layer** (compound/), not MemKraft core, because it needs domain context.

---

## 4. Integrated Benchmark Plan ("compound-brain validated")

Once Memanto auto-resolve (P1) + MaTTS true rollouts (P1) are in:

```
Baseline      : MemKraft v2.9.2 (current)
+ Memanto     : v2.9.2 + auto_resolve
+ MaTTS       : v2.9.2 + auto_resolve + tree rollouts
+ ScriptMem   : evaluate on LME-S + ScriptMem jointly
```

**Metrics:**
- LME-S accuracy (target: 56% → 76%+ post-Tier-1, → 80%+ post-Memanto/MaTTS)
- LME-S latency (p50, p99) — additive ergonomics shouldn't move these
- ScriptMem dep-chain accuracy (vs published SOTA)
- Internal: 5남매 trajectory success rate (cheap, free, large-N)

**Honest commitment:** publish a paper-quality comparison only when ≥3 of 4 metrics show clear lift. Otherwise keep iterating.

---

## 5. What Belongs Outside MemKraft

These appear in `garrytan/gbrain` but should *not* be absorbed into MemKraft core; they belong in the **5남매 / OpenClaw layer**:

- **idea-ingest / media-ingest / meeting-ingestion skills** — agent-layer pipelines, app-specific.
- **daily-task-manager / daily-task-prep** — workflow skills.
- **cron-scheduler / minion-orchestrator** — infra concerns.
- **publish skill** — sharing/UX concerns.
- **briefing / data-research / signal-detector** — domain skills.

Keep MemKraft as a **memory substrate library**. Skills are downstream concerns.

---

## 6. Recommended Sequence (Simon decision)

| Order | Item | ETA | Cost | Risk |
|---|---|---|---|---|
| 1 | **Memanto auto-resolve** (v2.10.0) | 1 day | $0 | low |
| 2 | **LME Tier 1 ablation** (A1+A2+A3) | 13h | ~$15 | low |
| 3 | **GBrain backlink-boosted ranking** | 1 day | ~$5 | low |
| 4 | **MaTTS spec document → code** | 3-5 days | ~$20 | medium |
| 5 | **ScriptMem adapter + eval** | 3 days | ~$30 | medium |
| 6 | **Typed predicates** (P3) | only after 1-5 ship | — | high |

**Single-day next step suggestion:** ship **Memanto auto-resolve as v2.10.0**. Smallest, cleanest, closes a curation queue item, and the policy design (recency / majority / confidence) is reusable across the other patches.

---

## Appendix — Why the previous Q3 plan changed

Earlier `MEMKRAFT_DECISION_2026-05-14.md` (Q3) suggested "ReasoningBank MaTTS as #1 next item." Today's v2.9.2 already shipped the ReasoningBank ergonomics wrapper, which was the prerequisite for true MaTTS but didn't include the multi-trajectory rollout itself. So the new #1 candidate is **Memanto auto-resolve** (cleaner, smaller, ships standalone), and MaTTS moves to position #4 with a proper design-first approach.
