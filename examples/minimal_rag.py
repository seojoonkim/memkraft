"""Minimal retrieval-augmented generation with MemKraft.

Idea: MemKraft is your retrieval layer; any LLM is the generator.

Run:
    OPENAI_API_KEY=sk-... MEMKRAFT_DIR=~/memory python examples/minimal_rag.py "Who runs Hashed?"

Requires: pip install openai memkraft
"""
from __future__ import annotations

import os
import sys

from memkraft import MemKraft

mk = MemKraft()  # respects MEMKRAFT_DIR env


def retrieve(query: str, k: int = 5) -> str:
    """Return top-k matching memory snippets as a single context block."""
    hits = mk.search(query, fuzzy=True)[:k]
    if not hits:
        return "(no prior memory found)"
    lines = []
    for h in hits:
        name = h.get("name") or h.get("entity") or "?"
        excerpt = h.get("excerpt") or h.get("snippet") or ""
        lines.append(f"- [{name}] {excerpt}")
    return "\n".join(lines)


def answer(query: str) -> str:
    from openai import OpenAI

    context = retrieve(query)
    client = OpenAI()
    resp = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": (
                "Answer using the MemKraft context below. "
                "If context is empty or irrelevant, say you don't know."
            )},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
        ],
    )
    return resp.choices[0].message.content or ""


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "What do I know about Hashed?"
    print(answer(q))
