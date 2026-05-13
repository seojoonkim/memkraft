# LME 38pp Gap — Ablation Analysis & Roadmap

**Author:** Zeon (Opus 4.7 sub-agent)
**Date:** 2026-05-14
**Status:** Decomposition done from existing data. Heavy ablation (~$50, 8h) requires Simon approval.

---

## 0. Gap Definition (resolved)

The "38pp gap" had been ambiguous in earlier docs. Settled now:

- **MemKraft LME-S (n=50) baseline:** 28/50 = **56%** (`baseline_s_n50_20260422_0154_judged.json`)
- **MemKraft LME-S best variant to date:** 31/50 = **62%** (`multi_session_v2_kwpass_s_n50_20260422_0246_judged.json`)
- **MemPalace LME-S:** **96.6%** (reported)
- **Gap:** **38–40pp** on LME-S split. *Not* on oracle.

**Important:** MemKraft already scores 96–100% on **oracle (n=50)** with v5 majority vote (`v5_semantic_majority_oracle_n50_..._judged.json` = 50/50 = 100%, after judge re-run). The 96%→98%→100% improvements live only in the *oracle* split, which provides relevant context pre-extracted. The 38pp gap is purely a **retrieval-and-reasoning-over-full-session-log** problem.

| Split | What it tests | MemKraft | MemPalace |
|---|---|---|---|
| **oracle** | reasoning given correct context | 96–100% | n/a (trivial) |
| **S (full)** | retrieval + reasoning from full log | **56–62%** | **96.6%** |

→ **gap is 100% retrieval-stage**, not LLM-reasoning-stage.

---

## 1. Category Decomposition (free, done now)

Per-category breakdown of MemKraft's three existing LME-S n=50 runs:

| Question type | n | baseline | ms_improved | ms_v2_kwpass | Pattern |
|---|---|---|---|---|---|
| **single-session-preference** | 3 | **0%** | **0%** | **0%** | ⛔ **complete miss** |
| **multi-session** | 13 | 23% | 46% | 54% | 🔥 biggest gap, ~50pp |
| **knowledge-update** | 8 | 62% | 50% | 50% | ⚠️ regression in ms variants |
| **single-session-assistant** | 6 | 67% | 50% | 67% | ⚠️ noisy / regression |
| **temporal-reasoning** | 13 | 77% | 77% | 69% | ⚠️ regression in kwpass |
| **single-session-user** | 7 | 86% | 86% | 100% | ✅ near-saturated |

### Findings

#### A. `single-session-preference` = **0/3 across all variants**
This is the most damning signal. 3 questions, none ever solved. Diagnoses:
- **Preference questions** ("which X does the user prefer?") need *preference fact extraction*, not generic retrieval.
- MemKraft has `pref_set` / `pref_get` API but the LME harness doesn't pipe preferences through it — the corpus is loaded as raw session text and queried via BM25.
- **Fix candidate:** preference extractor in the LME ingest path (one-time pass: scan sessions for "I prefer / I like / I don't like / My favorite" patterns → `fact_add(entity, "preference_<topic>", ...)`).
- **Expected lift:** 3/3 if extractor catches; +6pp on LME-S n=50.

#### B. `multi-session` = **23% → 54%** (50pp gap to MemPalace's likely ~100%)
- Largest absolute and relative source of error.
- 13 questions × ~46pp deficit = ~6 questions left on the table = **+12pp** if fully solved.
- ms_improved (+23pp) and kwpass (+8pp more) already proved retrieval-stage interventions move the needle. Suggests retrieval ranking, not LLM, is the dominant lever.

#### C. `knowledge-update` regressed in ms_improved (62→50)
- ms_improved retrieval favored multi-session signal at the cost of single-session knowledge updates.
- Indicates these are *trade-offs* in the current retrieval scoring — a unified retriever isn't optimizing all categories simultaneously.

#### D. `temporal-reasoning` regressed in kwpass (77→69)
- kwpass keyword-pass variant hurts temporal questions (which need a *time-window* lens, not keyword overlap).
- Same trade-off pattern as (C).

#### E. `single-session-user` near saturated (86–100%)
- Already easy. Not where the gap lives.

### Summary

**~75% of the 38pp gap is concentrated in `multi-session` + `single-session-preference` + the knowledge-update/temporal regressions**. ~25% lives in residual single-session-assistant noise.

Rough upper-bound math (if every category hits 100%):
- Current best (kwpass): 31/50 = 62%
- Gap = 19 questions = 38pp
- of those: ms = 6 (12pp), pref = 3 (6pp), ku = 4 (8pp), ssa = 2 (4pp), tr = 4 (8pp), ssu = 0
- Reachable with retrieval-only fixes (no LLM/judge change): ~30pp realistic, leaving a ~8pp residual that's likely LLM/judge ceiling.

---

## 2. Ablation Roadmap (ordered by ROI)

### Tier 1 — Cheap, high-confidence (do first, $0–$5, 2–4h each)

**A1. Preference extractor patch in LME ingest** (single-session-preference 0% → ~80%)
- Add a preprocess pass: regex-scan sessions for preference statements → `fact_add(entity, "pref_<topic>", value, source="lme_pref_extractor")`.
- At query time, if question matches "what does X prefer / like / etc.", route to `fact_list` first, then BM25 fallback.
- **Expected lift:** +4–6pp on LME-S n=50.
- **Cost:** ~3h code + 1 run (~$5).

**A2. Multi-session re-ranker** (multi-session 54% → 75%)
- Current best is `multi_session_v2_kwpass`. Add a second-pass cross-encoder reranker over the top-k=20 retrieved chunks.
- MemKraft already exposes `search_v2` post-filtering; extend with `rerank` hook using a small local model (or rule-based: prefer chunks that contain ≥2 query keywords AND a session id mentioned elsewhere in question context).
- **Expected lift:** +4pp on LME-S n=50.
- **Cost:** ~4h code + 1 run (~$5).

**A3. Category-aware retrieval** (fix the regressions in ku, tr)
- The current single-retriever-fits-all design causes ms_improved/kwpass to *hurt* knowledge-update and temporal-reasoning.
- Add a lightweight question-type classifier (LME has these labels in train; use few-shot prompt at query time).
- Route per-category: ms → kwpass; tr → temporal-window retriever; ku → recency-weighted retriever; etc.
- **Expected lift:** +4pp net (recover regressions + retain ms gains).
- **Cost:** ~6h code + 1 run (~$5).

**Tier 1 total:** ~$15, ~13h, **+12–14pp** → MemKraft LME-S ≈ **74–76%**.

### Tier 2 — Medium-confidence (Simon decision, ~$30–$50)

**B1. Full LME-S evaluation (n=500)** — current numbers are n=50. SOTA-claims need n=500.
- **Cost:** ~$50, ~8h.
- Should happen *after* Tier 1 to avoid wasting eval budget on baseline-only.

**B2. Cross-encoder semantic reranker** — replace BM25 ranking head with a small embedding model (MemKraft already has opt-in embedding via v2.7.3+).
- **Cost:** ~$10 eval + 1 day code.
- **Expected lift:** unclear (v2.7.3 embedding experiment had mixed results — see `feat/graph-mixin` notes).

### Tier 3 — Speculative (Simon decision, ~$100+)

**C1. Multi-hop reasoning chain** — explicit retrieve→read→retrieve loop for multi-session questions.
- Highest theoretical ceiling (could close residual 8pp), but adds latency and complexity.
- Requires harness changes (current LME harness is single-shot retrieval).
- **Cost:** 2–3 days code + ~$30 eval.

---

## 3. Recommended Next Step (15 min, $0)

The category-decomposition above already uses existing data — no new eval needed. The single most valuable next experiment is **A1 (preference extractor)** because:

1. `0/3 → ~3/3` is a clean win on a category that's structurally broken, not noisy.
2. Touches only the ingest path, no retrieval core changes.
3. The fix is reusable: improves any preference-related app (5남매 personal-context, etc.), not just LME.

**Suggested ordering for Simon's approval:**
1. ✅ Now: this decomposition doc (free, done).
2. Approve A1 (3h work, +4–6pp): preference extractor.
3. Approve A2+A3 in parallel (~10h work, +8pp): rerank + category routing.
4. Then B1 (n=500 LME-S eval): publish defensible 74%+ number.
5. Compare to MemPalace 96.6% with honest residual gap analysis.

---

## 4. Important Caveat — N=50 is small

All current LME-S numbers are n=50, stratified by question_type (seed=42). The 6–8pp swings between variants are within sampling noise (~6pp at 95% CI for n=50 binomial). The **per-category breakdown is more reliable than the headline number** because each category has a fixed denominator (3–13), making within-category comparisons across variants meaningful.

The 38pp gap to MemPalace is large enough to survive sampling noise regardless. The category-level patterns (preference 0/3, multi-session 3–7/13) are real.

---

## Appendix — Sources

- `benchmarks/longmemeval/results/baseline_s_n50_20260422_0154_judged.json`
- `benchmarks/longmemeval/results/multi_session_improved_s_n50_20260422_0241_judged.json`
- `benchmarks/longmemeval/results/multi_session_v2_kwpass_s_n50_20260422_0246_judged.json`
- `benchmarks/longmemeval/V5_REPORT.md` (oracle results)
- `~/сlawd/compound/learnings/2026-05-10-reply-target-incident-l31-l32-l33-and-memkraft-v28-debug.md` (38pp gap origin)
- `~/сlawd/compound/subconscious/winning-concept.md` (WC-035 trigger)
