#!/usr/bin/env python3
"""PersonaMem Benchmark Harness for MemKraft — v2

Strategy:
1. Extract persona profile from system message → MemKraft entity
2. Track preference changes across conversation sessions
3. Build structured context for each question
4. Compare: raw context vs MemKraft-enhanced context
"""

import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from memkraft import MemKraft

QTYPE_MAP = {
    "recall_user_shared_facts": "static_fact_recall",
    "recalling_facts_mentioned_by_the_user": "static_fact_recall",
    "acknowledge_latest_preferences": "preference_tracking",
    "track_full_preference_evolution": "preference_evolution",
    "revisit_reasons_behind_preference_updates": "preference_reasons",
    "provide_preference_aligned_recommendations": "aligned_recommendation",
    "suggest_new_ideas": "novel_suggestion",
    "generalizing_to_new_scenarios": "cross_domain_transfer",
}


def load_persona_mem(split: str = "32k") -> Tuple[List[Dict], Dict[str, List]]:
    from datasets import load_dataset
    from huggingface_hub import hf_hub_download

    ds = load_dataset("bowen-upenn/PersonaMem", "benchmark")
    questions = [dict(row) for row in ds[split]]

    context_path = hf_hub_download(
        "bowen-upenn/PersonaMem",
        f"shared_contexts_{split}.jsonl",
        repo_type="dataset"
    )

    contexts = {}
    with open(context_path, "r", encoding="utf-8") as f:
        for line in f:
            data = json.loads(line.strip())
            for key, val in data.items():
                contexts[key] = val

    return questions, contexts


def extract_persona_from_system(context: List[Dict]) -> Dict[str, str]:
    """Extract structured persona info from system message."""
    for msg in context:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            return parse_persona_text(content)
    return {}


def parse_persona_text(text: str) -> Dict[str, str]:
    """Parse persona description into structured fields."""
    info = {}

    # Name
    m = re.search(r"Name:\s*(.+?)[\n\r]", text)
    if m:
        info["name"] = m.group(1).strip()

    # Gender
    m = re.search(r"Gender Identity:\s*(.+?)[\n\r]", text)
    if m:
        info["gender"] = m.group(1).strip()

    # Age
    m = re.search(r"(?:aged?|is a)\s+(\d+)[\s-]+year", text)
    if m:
        info["age"] = m.group(1)

    # Race/Ethnicity
    m = re.search(r"Racial Identity:\s*(.+?)[\n\r]", text)
    if m:
        info["race"] = m.group(1).strip()

    # Profession
    m = re.search(r"(?:software engineer|content creator|teacher|doctor|lawyer|"
                   r"designer|writer|analyst|manager|consultant|therapist|"
                   r"photographer|musician|artist|chef|researcher|developer|"
                   r"professor|architect|journalist|entrepreneur|freelancer|"
                   r"student|retired|professional|seasoned .+?)(?:\s|,|\.|$)", text, re.I)
    if m:
        info["profession"] = m.group(0).strip().rstrip(",.")

    # Likes/preferences from persona description
    likes = re.findall(r"(?:love|loves|enjoy|enjoys|passionate about|keen expertise in|"
                       r"fond of|interested in|excited about)\s+(.+?)(?:\.|,|;|\n|$)", text, re.I)
    if likes:
        info["likes"] = [l.strip() for l in likes[:10]]

    # Dislikes
    dislikes = re.findall(r"(?:dislike|dislikes|hate|hates|avoid|avoids|not a fan of|"
                          r"doesn't like|don't enjoy)\s+(.+?)(?:\.|,|;|\n|$)", text, re.I)
    if dislikes:
        info["dislikes"] = [d.strip() for d in dislikes[:10]]

    return info


def extract_preferences_from_conversation(context: List[Dict]) -> List[Dict]:
    """Extract preference changes from conversation messages.

    PersonaMem conversations follow patterns like:
    - "I started doing X in YEAR"
    - "I then switched to Y"
    - "I also began Z"
    - "I love/enjoy/prefer X"
    """
    prefs = []
    current_topic = None

    for msg in context:
        if msg.get("role") != "user":
            continue

        content = msg.get("content", "")
        # Strip "User: " prefix if present
        if content.startswith("User: "):
            content = content[6:]

        # Pattern: temporal preference markers
        temporal_patterns = [
            # "I started creating/doing X in YEAR"
            (r"(?:I|i)\s+(?:started|began)\s+(.+?)\s+(?:in|back in|around)\s+(\d{4})", "started"),
            # "I then/also switched to/moved to X"
            (r"(?:I|i)\s+(?:then|also|now)\s+(?:switched to|moved to|began|started|"
             r"curated|created|explored|discovered|adopted)\s+(.+?)(?:\.|,|!|$)", "switched"),
            # "I love/enjoy/prefer X"
            (r"(?:I|i)\s+(?:love|enjoy|prefer|adore|appreciate)\s+(.+?)(?:\.|,|!|$)", "likes"),
            # "I don't like/dislike/hate X"
            (r"(?:I|i)\s+(?:don't like|dislike|hate|avoid)\s+(.+?)(?:\.|,|!|$)", "dislikes"),
            # "My favorite X is Y"
            (r"(?:my|My)\s+favorite\s+(.+?)\s+(?:is|are)\s+(.+?)(?:\.|,|!|$)", "favorite"),
        ]

        for pattern, pref_type in temporal_patterns:
            matches = re.finditer(pattern, content)
            for match in matches:
                if pref_type == "favorite":
                    key = match.group(1).strip()
                    value = match.group(2).strip()
                elif pref_type == "started":
                    value = match.group(1).strip()
                    year = match.group(2)
                    key = "activity"
                elif pref_type == "switched":
                    value = match.group(1).strip()
                    key = "activity"
                elif pref_type == "likes":
                    value = match.group(1).strip()
                    key = "likes"
                elif pref_type == "dislikes":
                    value = match.group(1).strip()
                    key = "dislikes"

                if len(value) > 3 and len(value) < 200:
                    # Determine category from content
                    category = infer_category(value + " " + content)
                    prefs.append({
                        "key": key,
                        "value": value,
                        "type": pref_type,
                        "category": category,
                        "year": match.group(2) if pref_type == "started" else None,
                    })

    return prefs


def infer_category(text: str) -> str:
    """Infer preference category from text content."""
    text_lower = text.lower()
    categories = {
        "food": ["food", "cuisine", "restaurant", "cooking", "diet", "meal", "eat", "recipe", "chef"],
        "music": ["music", "song", "playlist", "concert", "artist", "genre", "band", "album", "melody", "rhythm", "beat"],
        "travel": ["travel", "trip", "vacation", "hotel", "flight", "destination", "adventure", "explore"],
        "entertainment": ["movie", "show", "book", "game", "hobby", "sport", "film", "theater", "exhibit"],
        "work": ["work", "career", "job", "professional", "business", "project", "client"],
        "health": ["health", "fitness", "exercise", "wellness", "medical", "yoga", "running"],
        "education": ["education", "learning", "course", "study", "school", "university", "research"],
        "technology": ["tech", "software", "coding", "programming", "app", "digital", "computer", "AI"],
        "creative": ["art", "design", "photography", "writing", "painting", "drawing", "creative"],
    }

    for cat, keywords in categories.items():
        if any(kw in text_lower for kw in keywords):
            return cat

    return "general"


def inject_persona_to_memkraft(mk: MemKraft, persona_name: str,
                                persona_info: Dict[str, str],
                                context: List[Dict],
                                end_index: int) -> Dict[str, int]:
    """Inject persona + conversation into MemKraft."""
    mk.track(persona_name, entity_type="person", source="personamem")

    stats = {"facts": 0, "preferences": 0, "messages": 0}

    # Inject static persona facts
    if "profession" in persona_info:
        mk.update(persona_name, f"profession: {persona_info['profession']}", source="personamem")
        stats["facts"] += 1
    if "gender" in persona_info:
        mk.update(persona_name, f"gender: {persona_info['gender']}", source="personamem")
        stats["facts"] += 1
    if "age" in persona_info:
        mk.update(persona_name, f"age: {persona_info['age']}", source="personamem")
        stats["facts"] += 1
    if "likes" in persona_info:
        for like in persona_info["likes"]:
            mk.update(persona_name, f"likes: {like}", source="personamem")
            stats["facts"] += 1
    if "dislikes" in persona_info:
        for dislike in persona_info["dislikes"]:
            mk.update(persona_name, f"dislikes: {dislike}", source="personamem")
            stats["facts"] += 1

    # Extract and inject preferences from conversation
    messages = context[:end_index]
    prefs = extract_preferences_from_conversation(messages)

    for pref in prefs:
        mk.pref_set(
            persona_name,
            pref["key"],
            pref["value"],
            category=pref["category"],
            strength=0.8,
            source="personamem",
            valid_from=f"{pref['year']}-01-01" if pref.get("year") else None,
        )
        stats["preferences"] += 1

    # Count user messages
    for msg in messages:
        if msg.get("role") == "user":
            stats["messages"] += 1

    return stats


def build_memkraft_context(mk: MemKraft, persona_name: str,
                            question: str, topic: str) -> str:
    """Build structured context from MemKraft."""
    parts = []

    # 1. Entity brief
    brief = mk.brief(persona_name, save=False)
    if brief and len(brief) > 50:
        parts.append(brief[:2000])

    # 2. Current preferences
    prefs = mk.pref_get(persona_name)
    if prefs:
        pref_lines = []
        for p in prefs:
            line = f"- {p['key']}: {p['value']} (strength: {p['strength']:.1f})"
            pref_lines.append(line)
        parts.append("## Current Preferences\n" + "\n".join(pref_lines))

    # 3. Preference evolution
    for key in set(p["key"] for p in prefs):
        evolution = mk.pref_evolution(persona_name, key)
        if len(evolution) > 1:
            evo_lines = [f"## Preference Evolution: {key}"]
            for e in evolution:
                status = "CURRENT" if e["valid_to"] is None else f"until {e['valid_to']}"
                evo_lines.append(f"- {e['valid_from']} → {e['value']} [{status}]")
            parts.append("\n".join(evo_lines))

    # 4. Preference conflicts
    conflicts = mk.pref_conflicts(persona_name)
    if conflicts:
        conf_lines = ["## Preference Changes"]
        for c in conflicts:
            vals = " → ".join(f"{v['value']}({v['valid_from']})" for v in c["values"])
            conf_lines.append(f"- {c['key']}: {vals} → current: {c['current']}")
        parts.append("\n".join(conf_lines))

    return "\n\n".join(parts)


def run_benchmark(split: str = "32k",
                  max_questions: int = 0,
                  use_memkraft: bool = True,
                  model: str = "gpt-4o-mini") -> Dict[str, Any]:
    print(f"Loading PersonaMem {split} split...")
    questions, contexts = load_persona_mem(split)

    if max_questions > 0:
        questions = questions[:max_questions]

    print(f"Loaded {len(questions)} questions, {len(contexts)} contexts")

    mk_dir = f"/tmp/personamem-memkraft-{split}-{int(time.time())}"
    mk = MemKraft(base_dir=mk_dir)
    mk.init(verbose=False)

    injected_contexts = set()
    total_stats = {"facts": 0, "preferences": 0, "messages": 0}

    results = {"total": 0, "correct": 0, "by_type": {}, "errors": []}

    for i, q in enumerate(questions):
        qtype = q["question_type"]
        readable_type = QTYPE_MAP.get(qtype, qtype)
        shared_ctx_id = q["shared_context_id"]
        end_idx = int(q["end_index_in_shared_context"])

        ctx = contexts.get(shared_ctx_id, [])

        # Inject if needed
        if shared_ctx_id not in injected_contexts and ctx:
            persona_info = extract_persona_from_system(ctx)
            persona_name = persona_info.get("name", f"persona_{q['persona_id']}")

            if use_memkraft:
                stats = inject_persona_to_memkraft(mk, persona_name, persona_info, ctx, end_idx)
                for k, v in stats.items():
                    total_stats[k] += v

            injected_contexts.add(shared_ctx_id)
        else:
            persona_name = f"persona_{q['persona_id']}"

        # Build LLM context
        if use_memkraft:
            mk_context = build_memkraft_context(mk, persona_name,
                                                 q["user_question_or_message"],
                                                 q["topic"])
            llm_context = [{"role": "system", "content": f"You are a helpful assistant that knows the user well.\n\n{mk_context}"}]
        else:
            llm_context = ctx[:end_idx]

        # Query LLM
        try:
            answer = query_llm_for_answer(
                q["user_question_or_message"],
                q["all_options"],
                llm_context,
                model=model
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
            print(f"  [{i+1}/{len(questions)}] Accuracy: {acc:.1f}%")

    if results["total"] > 0:
        results["accuracy"] = results["correct"] / results["total"] * 100
    else:
        results["accuracy"] = 0.0

    for qtype, data in results["by_type"].items():
        if data["total"] > 0:
            data["accuracy"] = data["correct"] / data["total"] * 100

    results["injection_stats"] = total_stats
    results["memkraft_enabled"] = use_memkraft
    results["split"] = split
    results["model"] = model

    return results


def query_llm_for_answer(question: str, all_options: str,
                          context: List[Dict], model: str = "gpt-4o-mini") -> str:
    import openai

    instructions = (
        "Find the most appropriate model response and give your final answer "
        "(a), (b), (c), or (d) after the special token <final_answer>."
    )

    messages = context + [
        {"role": "user", "content": f"{question}\n\n{instructions}\n\n{all_options}"}
    ]

    client = openai.OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=1024,
    )

    return response.choices[0].message.content


def extract_answer(predicted: str, correct: str) -> bool:
    correct = correct.lower().strip("() ")

    pred = predicted.strip()
    if "<final_answer>" in pred:
        pred = pred.split("<final_answer>")[-1].strip()
    if pred.endswith("</final_answer>"):
        pred = pred[:-len("</final_answer>")].strip()

    # Remove HTML tags
    pred = re.sub(r"<[^>]+>", "", pred).strip()

    options = re.findall(r'\(([a-d])\)', pred.lower())
    if options:
        return options[-1] == correct

    letters = re.findall(r'\b([a-d])\b', pred.lower())
    if letters:
        return letters[-1] == correct

    return False


def print_results(results: Dict[str, Any]) -> None:
    mode = "MemKraft-Enhanced" if results["memkraft_enabled"] else "Baseline (Raw Context)"
    print(f"\n{'='*60}")
    print(f"PersonaMem Benchmark — {mode}")
    print(f"Split: {results['split']} | Model: {results['model']}")
    print(f"{'='*60}")
    print(f"Overall: {results['accuracy']:.1f}% ({results['correct']}/{results['total']})")

    if results.get("injection_stats"):
        s = results["injection_stats"]
        print(f"Injection: {s['facts']} facts, {s['preferences']} prefs from {s['messages']} msgs")

    print(f"\n{'Query Type':<35} {'Acc':>6} {'C/T':>8}")
    print("-" * 52)
    for qtype, data in sorted(results["by_type"].items()):
        acc = f"{data['accuracy']:.0f}%" if "accuracy" in data else "N/A"
        print(f"{qtype:<35} {acc:>6} {data['correct']:>3}/{data['total']:<3}")

    if results["errors"]:
        print(f"\n⚠️ {len(results['errors'])} errors")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="32k", choices=["32k", "128k", "1M"])
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--both", action="store_true")
    args = parser.parse_args()

    if args.both:
        print("\n🔵 BASELINE...")
        baseline = run_benchmark(args.split, args.max_questions, use_memkraft=False, model=args.model)
        print_results(baseline)

        print("\n🟢 MemKraft...")
        memkraft = run_benchmark(args.split, args.max_questions, use_memkraft=True, model=args.model)
        print_results(memkraft)

        print(f"\n{'='*60}")
        print("COMPARISON")
        print(f"{'='*60}")
        diff = memkraft["accuracy"] - baseline["accuracy"]
        print(f"Baseline: {baseline['accuracy']:.1f}%")
        print(f"MemKraft: {memkraft['accuracy']:.1f}%")
        print(f"Delta:    {'+' if diff >= 0 else ''}{diff:.1f}%")

        for qtype in set(list(baseline["by_type"].keys()) + list(memkraft["by_type"].keys())):
            b = baseline["by_type"].get(qtype, {}).get("accuracy", 0)
            m = memkraft["by_type"].get(qtype, {}).get("accuracy", 0)
            d = m - b
            arrow = "📈" if d > 0 else ("📉" if d < 0 else "➡️")
            print(f"  {arrow} {qtype:<35} {b:>5.1f}% → {m:>5.1f}% ({'+' if d >= 0 else ''}{d:.1f}%)")
    else:
        results = run_benchmark(args.split, args.max_questions,
                                use_memkraft=not args.baseline,
                                model=args.model)
        print_results(results)

        out_path = f"/Users/gimseojun/memcraft/benchmarks/personamem/results_{args.split}_{int(time.time())}.json"
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nSaved: {out_path}")
