"""
LLM-as-judge evaluator for LongMemEval harness.

Uses Claude Haiku to semantically judge whether a predicted answer
matches the gold answer, avoiding false negatives from contains_match
on formatting differences.
"""
import os
import json
import time
import glob
import sys
from typing import Optional

# Pluggable LLM backend — returns an object exposing `.messages.create(...)`
# in the Anthropic shape regardless of the underlying provider. Selected via
# MK_LME_LLM_BACKEND env (anthropic default).
from llm_backend import make_client_with_messages_api

client = make_client_with_messages_api()

JUDGE_PROMPT = """You are evaluating whether a predicted answer correctly answers a question based on the gold (correct) answer.

Question: {question}
Gold Answer: {gold}
Predicted Answer: {prediction}

Is the predicted answer correct? Consider:
- Semantic equivalence (e.g., "2 years" = "two years" = "24 months")
- Partial credit is NOT given — it's correct or not
- If gold says "I don't know" type answer and prediction also expresses uncertainty, that's correct
- Minor formatting differences don't matter — judge the content
- Predictions may include extra context or examples; that's fine as long as the gold's core content is present and not contradicted

SPECIAL RULE FOR PREFERENCE QUESTIONS (gold describes what the user "would prefer"):
- Mark correct if the prediction captures the SAME core preference signals as the gold, even with different wording or extra/different examples
- Paraphrases count: "hot tub on balcony" ≡ "hot tubs on private balconies"; "podcasts in history genre" ≡ "podcasts, especially history"
- Subsuming generalizations count: "audio-based activities including podcasts (history, ...)" covers "podcasts/audiobooks, especially history"
- Mark incorrect ONLY if the prediction contradicts the gold or misses the gold's central preference theme

Respond with ONLY: "correct" or "incorrect"
"""


_DEFAULT_JUDGE_MODEL = os.environ.get("JUDGE_MODEL", os.environ.get("MODEL", "claude-haiku-4-5"))


def llm_judge(
    question: str,
    gold: str,
    prediction: str,
    model: str = _DEFAULT_JUDGE_MODEL,
    max_retries: int = 3,
) -> Optional[bool]:
    """
    Returns True if correct, False if incorrect, None on API failure.

    Provider is selected via MK_LME_LLM_BACKEND (default: anthropic). The
    `model` argument is forwarded; non-Anthropic backends substitute their
    own default if `model` is empty (see llm_backend.LLMClient).
    """
    # If env overrides the backend, respect its default model rather than the
    # legacy claude-haiku id passed in by callers.
    env_model = os.environ.get("MK_LME_LLM_MODEL")
    if client.backend != "anthropic":
        effective_model = env_model or client.model
    else:
        effective_model = model

    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=effective_model,
                max_tokens=10,
                messages=[{
                    "role": "user",
                    "content": JUDGE_PROMPT.format(
                        question=question,
                        gold=gold,
                        prediction=prediction,
                    ),
                }],
            )
            verdict = response.content[0].text.strip().lower()
            return "correct" in verdict and "incorrect" not in verdict.split()[0:1]
        except client.RateLimitError as e:  # noqa: PERF203
            wait = 2 ** attempt
            print(f"  [judge] 429 rate limit, wait {wait}s (attempt {attempt+1}/{max_retries})")
            time.sleep(wait)
        except Exception as e:
            print(f"  [judge] error: {e} (attempt {attempt+1}/{max_retries})")
            time.sleep(1)
    return None


def score_with_judge(results: list, model: str = _DEFAULT_JUDGE_MODEL) -> dict:
    """Re-score all results using LLM-as-judge."""
    categories: dict = {}
    total_correct = 0
    total_judged = 0
    errors = 0

    for i, r in enumerate(results):
        verdict = llm_judge(
            r.get("question", ""),
            r.get("answer", ""),
            r.get("prediction", ""),
            model=model,
        )
        if verdict is None:
            r["llm_judge"] = None
            errors += 1
        else:
            r["llm_judge"] = bool(verdict)
            total_judged += 1
            if verdict:
                total_correct += 1

        cat = r.get("question_type", r.get("category", "unknown"))
        if cat not in categories:
            categories[cat] = {"correct": 0, "total": 0, "errors": 0}
        if verdict is None:
            categories[cat]["errors"] += 1
        else:
            categories[cat]["total"] += 1
            categories[cat]["correct"] += int(verdict)

        if (i + 1) % 10 == 0:
            running = total_correct / total_judged if total_judged else 0.0
            print(f"  [{i+1}/{len(results)}] running accuracy: {running:.1%} (errors: {errors})")

    n = len(results)
    return {
        "total": n,
        "judged": total_judged,
        "errors": errors,
        "llm_judge_score": (total_correct / total_judged) if total_judged > 0 else 0.0,
        "by_category": {
            cat: {
                "score": (v["correct"] / v["total"]) if v["total"] > 0 else 0.0,
                "correct": v["correct"],
                "total": v["total"],
                "errors": v["errors"],
            }
            for cat, v in categories.items()
        },
        "judge_model": model,
    }


def rejudge_file(path: str, model: str = _DEFAULT_JUDGE_MODEL) -> str:
    """Re-judge a result file and save alongside with _judged suffix."""
    print(f"\n📂 Loading: {path}")
    with open(path) as f:
        data = json.load(f)

    results = data.get("results", [])
    print(f"   Samples: {len(results)}")
    print(f"   Judge model: {model}\n")

    judge_scores = score_with_judge(results, model=model)

    print("\n📊 LLM-as-judge results:")
    print(f"   Score: {judge_scores['llm_judge_score']:.1%}  ({judge_scores['judged']} judged, {judge_scores['errors']} errors)")
    original = data.get("scores", {}).get("score")
    if original is not None:
        print(f"   Original contains_match: {original:.1%}")
    print("\n   By category:")
    for cat, v in judge_scores["by_category"].items():
        print(f"     {cat}: {v['score']:.1%}  ({v['correct']}/{v['total']})")

    data["llm_judge_scores"] = judge_scores
    out_path = path.replace(".json", "_judged.json")
    with open(out_path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Saved: {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        # default: latest oracle and s result files
        oracle_files = sorted(glob.glob("results/baseline_oracle_n*.json"))
        s_files = sorted(glob.glob("results/baseline_s_n*.json"))
        targets = []
        if oracle_files:
            targets.append(oracle_files[-1])
        if s_files:
            targets.append(s_files[-1])

    outputs = []
    for t in targets:
        out = rejudge_file(t)
        outputs.append(out)

    print("\n✅ Done. Judged files:")
    for o in outputs:
        print(f"   {o}")
