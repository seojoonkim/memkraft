#!/usr/bin/env python3
"""PersonaMem Benchmark — A/B/C Comparison

A: Baseline (raw conversation context)
B: Hybrid (raw context + MemKraft structured summary prepended)
C: MemKraft-only (structured summary replaces raw context)
"""

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
from memkraft import MemKraft
from harness import (
    load_persona_mem, extract_persona_from_system, parse_persona_text,
    inject_persona_to_memkraft, build_memkraft_context,
    query_llm_for_answer, extract_answer, QTYPE_MAP
)


def build_hybrid_context(mk: MemKraft, persona_name: str,
                          raw_context: List[Dict], end_idx: int,
                          question: str, topic: str) -> List[Dict]:
    """Hybrid: raw context + MemKraft summary prepended as system."""
    # Get MemKraft summary
    mk_summary = build_memkraft_context(mk, persona_name, question, topic)

    # Prepend as system message before raw context
    system_msg = {
        "role": "system",
        "content": (
            "You are a helpful assistant that knows the user well. "
            "Below is a structured summary of the user's profile and preferences, "
            "followed by the full conversation history.\n\n"
            f"=== USER PROFILE SUMMARY ===\n{mk_summary}\n=== END SUMMARY ==="
        )
    }

    # Raw context without original system message (we replace it)
    raw_without_system = [m for m in raw_context[:end_idx] if m.get("role") != "system"]

    return [system_msg] + raw_without_system


def run_variant(name: str, questions: List[Dict], contexts: Dict,
                mk: MemKraft, mode: str, model: str,
                injected: set, total_stats: Dict) -> Dict[str, Any]:
    """Run a single variant (A/B/C)."""
    results = {"total": 0, "correct": 0, "by_type": {}, "errors": [], "mode": mode}

    for i, q in enumerate(questions):
        qtype = q["question_type"]
        readable_type = QTYPE_MAP.get(qtype, qtype)
        shared_ctx_id = q["shared_context_id"]
        end_idx = int(q["end_index_in_shared_context"])
        ctx = contexts.get(shared_ctx_id, [])

        # Ensure MemKraft has this context (for B and C)
        if mode != "baseline" and shared_ctx_id not in injected:
            persona_info = extract_persona_from_system(ctx)
            persona_name = persona_info.get("name", f"persona_{q['persona_id']}")
            stats = inject_persona_to_memkraft(mk, persona_name, persona_info, ctx, end_idx)
            for k, v in stats.items():
                total_stats[k] += v
            injected.add(shared_ctx_id)

        persona_info = extract_persona_from_system(ctx)
        persona_name = persona_info.get("name", f"persona_{q['persona_id']}")

        # Build context based on mode
        if mode == "baseline":
            llm_context = ctx[:end_idx]
        elif mode == "hybrid":
            llm_context = build_hybrid_context(mk, persona_name, ctx, end_idx,
                                                q["user_question_or_message"], q["topic"])
        elif mode == "memkraft":
            mk_ctx = build_memkraft_context(mk, persona_name,
                                             q["user_question_or_message"], q["topic"])
            llm_context = [{"role": "system", "content": f"You are a helpful assistant that knows the user well.\n\n{mk_ctx}"}]

        # Query
        try:
            answer = query_llm_for_answer(
                q["user_question_or_message"], q["all_options"],
                llm_context, model=model
            )
            correct = extract_answer(answer, q["correct_answer"])
        except Exception as e:
            results["errors"].append({"question_id": q["question_id"], "error": str(e)})
            correct = False

        results["total"] += 1
        if correct:
            results["correct"] += 1

        if readable_type not in results["by_type"]:
            results["by_type"][readable_type] = {"total": 0, "correct": 0}
        results["by_type"][readable_type]["total"] += 1
        if correct:
            results["by_type"][readable_type]["correct"] += 1

        if (i + 1) % 20 == 0:
            acc = results["correct"] / results["total"] * 100
            print(f"    [{name}] {i+1}/{len(questions)} — {acc:.1f}%")

    if results["total"] > 0:
        results["accuracy"] = results["correct"] / results["total"] * 100
    for data in results["by_type"].values():
        if data["total"] > 0:
            data["accuracy"] = data["correct"] / data["total"] * 100

    return results


def run_comparison(split: str = "32k", max_questions: int = 0,
                   model: str = "gpt-4o-mini"):
    """Run all 3 variants and compare."""
    print(f"Loading PersonaMem {split}...")
    questions, contexts = load_persona_mem(split)
    if max_questions > 0:
        questions = questions[:max_questions]
    print(f"Running {len(questions)} questions × 3 variants\n")

    mk_dir = f"/tmp/personamem-compare-{split}-{int(time.time())}"
    mk = MemKraft(base_dir=mk_dir)
    mk.init(verbose=False)

    injected = set()
    stats = {"facts": 0, "preferences": 0, "messages": 0}

    # A: Baseline
    print("🔵 A: Baseline (raw context)...")
    a = run_variant("A", questions, contexts, mk, "baseline", model, injected, stats)

    # B: Hybrid
    print("🟢 B: Hybrid (raw + MemKraft summary)...")
    b = run_variant("B", questions, contexts, mk, "hybrid", model, injected, stats)

    # C: MemKraft-only
    print("🟡 C: MemKraft-only (structured summary)...")
    c = run_variant("C", questions, contexts, mk, "memkraft", model, injected, stats)

    # Print comparison
    print(f"\n{'='*70}")
    print(f"PersonaMem A/B/C Comparison — {split} — {model}")
    print(f"{'='*70}")
    print(f"MemKraft injection: {stats['facts']} facts, {stats['preferences']} prefs")
    print(f"\n{'Variant':<20} {'Accuracy':>10} {'Correct':>10} {'Total':>8}")
    print("-" * 50)
    for name, res in [("A: Baseline", a), ("B: Hybrid", b), ("C: MemKraft", c)]:
        print(f"{name:<20} {res['accuracy']:>9.1f}% {res['correct']:>10} {res['total']:>8}")

    # Per-type comparison
    all_types = set()
    for res in [a, b, c]:
        all_types.update(res["by_type"].keys())

    print(f"\n{'Query Type':<35} {'A':>6} {'B':>6} {'C':>6} {'Best':>6}")
    print("-" * 62)
    for qtype in sorted(all_types):
        a_acc = a["by_type"].get(qtype, {}).get("accuracy", 0)
        b_acc = b["by_type"].get(qtype, {}).get("accuracy", 0)
        c_acc = c["by_type"].get(qtype, {}).get("accuracy", 0)
        best = max(a_acc, b_acc, c_acc)
        marker = "A" if best == a_acc else ("B" if best == b_acc else "C")
        print(f"{qtype:<35} {a_acc:>5.0f}% {b_acc:>5.0f}% {c_acc:>5.0f}% {marker:>6}")

    # Save
    out = {
        "split": split, "model": model, "n_questions": len(questions),
        "injection_stats": stats,
        "baseline": a, "hybrid": b, "memkraft_only": c,
    }
    out_path = f"/Users/gimseojun/memcraft/benchmarks/personamem/comparison_{split}_{int(time.time())}.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved: {out_path}")

    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="32k", choices=["32k", "128k", "1M"])
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--model", default="gpt-4o-mini")
    args = parser.parse_args()

    run_comparison(args.split, args.max_questions, args.model)
