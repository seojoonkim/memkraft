#!/usr/bin/env python3
"""PersonaMem Benchmark Harness — v3

Key improvements over v2:
  1. 3-variant comparison in a single pass: baseline / hybrid / memkraft
  2. Per-context ingestion via PersonaMemAdapter (extracts from every msg)
  3. Per-question-type structured context
  4. Intermediate result checkpointing for crash recovery
  5. Retry/backoff on OpenAI failures
  6. Clean CLI: --split 32k|128k|1M, --variants baseline,hybrid,memkraft
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make memkraft importable when run from benchmarks/personamem/
HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from memkraft import MemKraft
from memkraft.personamem import (
    PersonaMemAdapter,
    build_context,
    QUESTION_TYPES,
)


QTYPE_MAP = {
    "recall_user_shared_facts": "static_fact_recall",
    "recalling_facts_mentioned_by_the_user": "static_fact_recall",
    "acknowledge_latest_preferences": "preference_tracking",
    "track_full_preference_evolution": "preference_evolution",
    "revisit_reasons_behind_preference_updates": "preference_reasons",
    "recalling_the_reasons_behind_previous_updates": "preference_reasons",
    "provide_preference_aligned_recommendations": "aligned_recommendation",
    "suggest_new_ideas": "novel_suggestion",
    "generalizing_to_new_scenarios": "cross_domain_transfer",
}


# ────────────────────────────────────────────────────────────
# Dataset loading
# ────────────────────────────────────────────────────────────

def load_persona_mem(split: str = "32k") -> Tuple[List[Dict], Dict[str, List]]:
    """Load PersonaMem questions + shared contexts."""
    from datasets import load_dataset
    from huggingface_hub import hf_hub_download

    ds = load_dataset("bowen-upenn/PersonaMem", "benchmark")
    questions = [dict(row) for row in ds[split]]

    context_path = hf_hub_download(
        "bowen-upenn/PersonaMem",
        f"shared_contexts_{split}.jsonl",
        repo_type="dataset",
    )

    contexts: Dict[str, List[Dict]] = {}
    with open(context_path, "r", encoding="utf-8") as f:
        for line in f:
            data = json.loads(line.strip())
            for key, val in data.items():
                contexts[key] = val

    return questions, contexts


# ────────────────────────────────────────────────────────────
# LLM query + answer extraction
# ────────────────────────────────────────────────────────────

def query_llm(
    question: str,
    all_options: str,
    messages: List[Dict[str, str]],
    model: str = "gpt-4o-mini",
    max_retries: int = 3,
) -> str:
    """Query OpenAI (or OpenRouter) chat completions with simple retry/backoff."""
    import openai
    import os

    instructions = (
        "Find the most appropriate model response and give your final answer "
        "(a), (b), (c), or (d) after the special token <final_answer>."
    )

    payload = messages + [
        {"role": "user", "content": f"{question}\n\n{instructions}\n\n{all_options}"}
    ]

    provider = os.environ.get("MEMCRAFT_LLM_PROVIDER", "").lower()
    # codex CLI branch (Day 2.5): ChatGPT OAuth via subprocess (lazy import)
    if provider == "codex" or os.environ.get("MEMCRAFT_USE_CODEX"):
        from harness_validator import CodexSubprocessAdapter  # type: ignore
        client = CodexSubprocessAdapter()
    else:
        # litellm-vhh branch (Day 3)
        if provider == "litellm-vhh" or os.environ.get("LITELLM_VHH_KEY"):
            client = openai.OpenAI(
                base_url="https://llm.vhh.sh/v1",
                api_key=os.environ.get(
                    "LITELLM_VHH_KEY",
                    "sk-litellm-local-58e6dff127b675454d6cc518918738974c67fb9395b47ebd",
                ),
                default_headers={"User-Agent": "OpenAI/Python 1.50.0"},
            )
        else:
            use_or = provider == "openrouter" or (
                "/" in str(model) and os.environ.get("OPENROUTER_API_KEY")
            )
            if use_or:
                client = openai.OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=os.environ["OPENROUTER_API_KEY"],
                )
            else:
                client = openai.OpenAI()
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=payload,
                max_completion_tokens=1024,
            )
            return resp.choices[0].message.content
        except Exception as e:  # noqa: BLE001
            last_err = e
            # Exponential backoff
            wait = 2 ** attempt
            time.sleep(wait)
    raise last_err or RuntimeError("LLM query failed after retries")


def extract_answer(predicted: str, correct: str) -> bool:
    correct = (correct or "").lower().strip("() ")
    pred = (predicted or "").strip()
    if "<final_answer>" in pred:
        pred = pred.split("<final_answer>")[-1].strip()
    if pred.endswith("</final_answer>"):
        pred = pred[: -len("</final_answer>")].strip()
    pred = re.sub(r"<[^>]+>", "", pred).strip()
    opts = re.findall(r"\(([a-d])\)", pred.lower())
    if opts:
        return opts[-1] == correct
    letters = re.findall(r"\b([a-d])\b", pred.lower())
    if letters:
        return letters[-1] == correct
    return False


# ────────────────────────────────────────────────────────────
# Context builders per variant
# ────────────────────────────────────────────────────────────

def build_baseline_context(raw_context: List[Dict], end_idx: int) -> List[Dict]:
    """A: raw conversation up to end_idx."""
    return raw_context[:end_idx]


def build_memkraft_only_context(
    mk: MemKraft,
    persona_name: str,
    question_type: str,
    topic: str,
    question: str,
    statements: List[Dict[str, Any]],
) -> List[Dict]:
    """C: MemKraft structured summary only (no raw conversation)."""
    mk_ctx = build_context(
        mk, persona_name, question_type, topic, question, statements=statements
    )
    system = (
        "You are a helpful assistant that knows the user well. "
        "Below is a structured summary of everything we know about this user. "
        "Use it to answer the next question.\n\n"
        f"{mk_ctx}"
    )
    return [{"role": "system", "content": system}]


def build_hybrid_context(
    mk: MemKraft,
    persona_name: str,
    question_type: str,
    topic: str,
    question: str,
    statements: List[Dict[str, Any]],
    raw_context: List[Dict],
    end_idx: int,
) -> List[Dict]:
    """B: MemKraft summary prepended, then raw conversation."""
    mk_ctx = build_context(
        mk, persona_name, question_type, topic, question, statements=statements
    )
    system = (
        "You are a helpful assistant that knows the user well. "
        "The STRUCTURED SUMMARY below distills the user's profile, "
        "preferences, and how they have evolved. The full conversation "
        "history follows afterwards. Rely on the summary first; use the "
        "history to resolve any ambiguity.\n\n"
        f"=== STRUCTURED SUMMARY ===\n{mk_ctx}\n=== END SUMMARY ==="
    )
    raw_wo_system = [m for m in raw_context[:end_idx] if m.get("role") != "system"]
    return [{"role": "system", "content": system}] + raw_wo_system


# ────────────────────────────────────────────────────────────
# Benchmark runner
# ────────────────────────────────────────────────────────────

def _empty_results(variant: str) -> Dict[str, Any]:
    return {
        "variant": variant,
        "total": 0,
        "correct": 0,
        "by_type": {},
        "errors": [],
    }


def _accumulate(res: Dict[str, Any], qtype: str, is_correct: bool) -> None:
    res["total"] += 1
    if is_correct:
        res["correct"] += 1
    bt = res["by_type"].setdefault(qtype, {"total": 0, "correct": 0})
    bt["total"] += 1
    if is_correct:
        bt["correct"] += 1


def _finalize(res: Dict[str, Any]) -> None:
    res["accuracy"] = (res["correct"] / res["total"] * 100) if res["total"] else 0.0
    for d in res["by_type"].values():
        d["accuracy"] = (d["correct"] / d["total"] * 100) if d["total"] else 0.0


def _load_checkpoint(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_checkpoint(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def run_benchmark(
    split: str = "32k",
    max_questions: int = 0,
    variants: Optional[List[str]] = None,
    model: str = "gpt-4o-mini",
    checkpoint_path: Optional[Path] = None,
    checkpoint_every: int = 10,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Run the benchmark across the requested variants."""
    variants = variants or ["baseline", "hybrid", "memkraft"]
    valid = {"baseline", "hybrid", "memkraft"}
    for v in variants:
        if v not in valid:
            raise ValueError(f"Unknown variant: {v} (must be one of {valid})")

    if verbose:
        print(f"Loading PersonaMem {split}...")
    questions, contexts = load_persona_mem(split)
    if max_questions > 0:
        questions = questions[:max_questions]
    if verbose:
        print(f"Loaded {len(questions)} questions / {len(contexts)} contexts")

    # Shared MemKraft — one per run (ingest each context once)
    mk_dir = f"/tmp/personamem-v3-{split}-{int(time.time())}"
    mk = MemKraft(base_dir=mk_dir)
    mk.init(verbose=False)
    adapter = PersonaMemAdapter(mk)

    # Cache: shared_context_id → ingestion result (persona_name + statements)
    ingestion_cache: Dict[str, Dict[str, Any]] = {}

    # Result storage
    results: Dict[str, Dict[str, Any]] = {v: _empty_results(v) for v in variants}

    # Checkpoint — stored per-question index
    completed_ids: set = set()
    ckpt_data: Dict[str, Any] = {}
    if checkpoint_path:
        ckpt_data = _load_checkpoint(checkpoint_path)
        if ckpt_data:
            completed_ids = set(ckpt_data.get("completed_ids", []))
            for v in variants:
                if v in ckpt_data.get("results", {}):
                    results[v] = ckpt_data["results"][v]
            if verbose:
                print(f"Resumed checkpoint: {len(completed_ids)} completed")

    start_ts = time.time()
    run_stats = {"ingestions": 0, "total_statements": 0,
                 "total_preferences": 0, "total_facts": 0}

    # v3 majority-vote support (2026-04-27): track per-question outcome
    # per variant so an outer driver (e.g. run_full.py --majority-vote)
    # can fuse multiple runs. Additive only — the existing aggregate
    # `results[v]['correct']` / `accuracy` numbers are unchanged.
    per_question: Dict[str, Dict[str, bool]] = {v: {} for v in variants}

    for i, q in enumerate(questions):
        qid = q.get("question_id", str(i))
        if qid in completed_ids:
            continue

        qtype = q["question_type"]
        readable = QTYPE_MAP.get(qtype, qtype)
        shared_ctx_id = q["shared_context_id"]
        end_idx = int(q["end_index_in_shared_context"])
        topic = q.get("topic", "")
        question = q["user_question_or_message"]
        options = q["all_options"]
        correct = q["correct_answer"]
        ctx = contexts.get(shared_ctx_id, [])

        # Ingest MemKraft once per (shared_ctx_id, end_idx) pair
        cache_key = f"{shared_ctx_id}::{end_idx}"
        if cache_key not in ingestion_cache and ctx:
            # Suppress MemKraft's chatty stdout during ingestion
            import io, contextlib
            _buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(_buf):
                    ing = adapter.ingest(
                        ctx, end_idx,
                        persona_name_fallback=f"persona_{q.get('persona_id','x')}",
                    )
                ingestion_cache[cache_key] = ing
                run_stats["ingestions"] += 1
                run_stats["total_statements"] += ing["stats"]["statements"]
                run_stats["total_preferences"] += ing["stats"]["preferences"]
                run_stats["total_facts"] += ing["stats"]["facts"]
            except Exception as e:  # noqa: BLE001  # noqa: F841
                if verbose:
                    print(f"  ⚠️ ingestion failed for {shared_ctx_id}: {e}")
                ingestion_cache[cache_key] = {
                    "persona_name": f"persona_{q.get('persona_id','x')}",
                    "persona_info": {},
                    "stats": {"facts": 0, "preferences": 0, "messages": 0,
                               "sessions": 0, "statements": 0},
                    "statements": [],
                    "sessions": [],
                }

        ing = ingestion_cache.get(cache_key, {
            "persona_name": f"persona_{q.get('persona_id','x')}",
            "statements": [],
        })
        persona_name = ing.get("persona_name") or f"persona_{q.get('persona_id','x')}"
        statements = ing.get("statements") or []

        # Run each variant
        for variant in variants:
            try:
                if variant == "baseline":
                    msgs = build_baseline_context(ctx, end_idx)
                elif variant == "hybrid":
                    msgs = build_hybrid_context(
                        mk, persona_name, qtype, topic, question,
                        statements, ctx, end_idx,
                    )
                elif variant == "memkraft":
                    msgs = build_memkraft_only_context(
                        mk, persona_name, qtype, topic, question, statements,
                    )
                else:
                    continue

                answer = query_llm(question, options, msgs, model=model)
                correct_flag = extract_answer(answer, correct)
                _accumulate(results[variant], readable, correct_flag)
                per_question[variant][qid] = bool(correct_flag)
            except Exception as e:  # noqa: BLE001
                results[variant]["errors"].append({
                    "question_id": qid, "error": str(e),
                })
                _accumulate(results[variant], readable, False)
                per_question[variant][qid] = False

        completed_ids.add(qid)

        # Progress + checkpoint
        if verbose and (i + 1) % 10 == 0:
            per_variant = " | ".join(
                f"{v}: {results[v]['correct']}/{results[v]['total']}"
                for v in variants
            )
            print(f"  [{i+1}/{len(questions)}] {per_variant}")

        if checkpoint_path and (i + 1) % checkpoint_every == 0:
            _save_checkpoint(checkpoint_path, {
                "completed_ids": sorted(completed_ids),
                "results": results,
                "run_stats": run_stats,
                "split": split,
                "model": model,
                "variants": variants,
                "elapsed_seconds": time.time() - start_ts,
            })

    # Finalize
    for v in variants:
        _finalize(results[v])

    if checkpoint_path:
        _save_checkpoint(checkpoint_path, {
            "completed_ids": sorted(completed_ids),
            "results": results,
            "run_stats": run_stats,
            "split": split,
            "model": model,
            "variants": variants,
            "elapsed_seconds": time.time() - start_ts,
            "finished": True,
        })

    return {
        "split": split,
        "model": model,
        "variants": variants,
        "n_questions": len(questions),
        "n_completed": len(completed_ids),
        "run_stats": run_stats,
        "results": results,
        "per_question": per_question,
        "elapsed_seconds": time.time() - start_ts,
        "memkraft_dir": mk_dir,
    }


# ────────────────────────────────────────────────────────────
# Reporting
# ────────────────────────────────────────────────────────────

def print_report(run: Dict[str, Any]) -> None:
    variants = run["variants"]
    results = run["results"]

    print("\n" + "=" * 72)
    print(f"PersonaMem v3 Harness | split={run['split']} | model={run['model']}")
    print(f"Questions: {run['n_completed']}/{run['n_questions']} "
          f"| elapsed: {run['elapsed_seconds']:.1f}s")
    rs = run.get("run_stats", {})
    if rs:
        print(f"Ingestions: {rs.get('ingestions', 0)} | "
              f"statements: {rs.get('total_statements', 0)} | "
              f"preferences: {rs.get('total_preferences', 0)} | "
              f"facts: {rs.get('total_facts', 0)}")
    print("=" * 72)

    # Overall table
    print(f"\n{'Variant':<14} {'Accuracy':>10} {'Correct':>10} {'Total':>8} "
          f"{'Errors':>8}")
    print("-" * 54)
    for v in variants:
        r = results[v]
        print(f"{v:<14} {r['accuracy']:>9.1f}% {r['correct']:>10} "
              f"{r['total']:>8} {len(r['errors']):>8}")

    # Per-type table
    all_types = set()
    for v in variants:
        all_types.update(results[v]["by_type"].keys())

    if all_types:
        header = f"\n{'Query Type':<32}"
        for v in variants:
            header += f" {v:>10}"
        print(header)
        print("-" * (32 + 11 * len(variants)))
        for qt in sorted(all_types):
            row = f"{qt:<32}"
            for v in variants:
                bt = results[v]["by_type"].get(qt, {})
                acc = bt.get("accuracy")
                if acc is None:
                    row += f" {'--':>10}"
                else:
                    row += f" {acc:>9.1f}%"
            print(row)

    # Delta summary vs baseline
    if "baseline" in variants and len(variants) > 1:
        b_acc = results["baseline"]["accuracy"]
        print("\nDelta vs baseline:")
        for v in variants:
            if v == "baseline":
                continue
            d = results[v]["accuracy"] - b_acc
            sign = "+" if d >= 0 else ""
            arrow = "📈" if d > 0 else ("📉" if d < 0 else "➡️")
            print(f"  {arrow} {v:<14} {sign}{d:.1f}%")


# ────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="PersonaMem Benchmark v3")
    parser.add_argument("--split", default="32k", choices=["32k", "128k", "1M"])
    parser.add_argument("--max-questions", type=int, default=0,
                        help="Limit number of questions (0 = all)")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--variants",
                        default="baseline,hybrid,memkraft",
                        help="Comma-separated: baseline,hybrid,memkraft")
    parser.add_argument("--both", action="store_true",
                        help="Shorthand for --variants baseline,hybrid,memkraft")
    parser.add_argument("--baseline", action="store_true",
                        help="Shorthand for --variants baseline")
    parser.add_argument("--memkraft-only", action="store_true",
                        help="Shorthand for --variants memkraft")
    parser.add_argument("--checkpoint",
                        default=None,
                        help="Path to checkpoint file (resume + save progress)")
    parser.add_argument("--checkpoint-every", type=int, default=10)
    parser.add_argument("--out", default=None,
                        help="Path to save final JSON results")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.both:
        variants = ["baseline", "hybrid", "memkraft"]
    elif args.baseline:
        variants = ["baseline"]
    elif args.memkraft_only:
        variants = ["memkraft"]
    else:
        variants = [v.strip() for v in args.variants.split(",") if v.strip()]

    ckpt = Path(args.checkpoint) if args.checkpoint else None
    if ckpt:
        ckpt.parent.mkdir(parents=True, exist_ok=True)

    run = run_benchmark(
        split=args.split,
        max_questions=args.max_questions,
        variants=variants,
        model=args.model,
        checkpoint_path=ckpt,
        checkpoint_every=args.checkpoint_every,
        verbose=not args.quiet,
    )

    print_report(run)

    out_path = args.out or (
        f"{HERE}/results_v3_{args.split}_{int(time.time())}.json"
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(run, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
