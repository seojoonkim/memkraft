"""Expose MemKraft as OpenAI function-calling tools.

Run:

    OPENAI_API_KEY=sk-... MEMKRAFT_DIR=~/memory python examples/openai_function_calling.py

Requires: pip install openai memkraft
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from memkraft import MemKraft

# --- 1. Tool schemas ---------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memkraft_remember",
            "description": "Store new information about an entity (person/org/project).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "info": {"type": "string"},
                    "source": {"type": "string", "default": "chat"},
                },
                "required": ["name", "info"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memkraft_search",
            "description": "Hybrid search across all stored memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "fuzzy": {"type": "boolean", "default": True},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memkraft_recall",
            "description": "Return compiled state + timeline for an entity.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
]


# --- 2. Dispatcher -----------------------------------------------------------

mk = MemKraft()  # respects MEMKRAFT_DIR env


def dispatch(name: str, arguments: Dict[str, Any]) -> Any:
    if name == "memkraft_remember":
        mk.update(arguments["name"], arguments["info"],
                  source=arguments.get("source", "chat"))
        return {"ok": True, "name": arguments["name"]}
    if name == "memkraft_search":
        return mk.search(arguments["query"], fuzzy=arguments.get("fuzzy", True))
    if name == "memkraft_recall":
        return mk.brief(arguments["name"]) or {"found": False}
    raise ValueError(f"unknown tool: {name}")


# --- 3. Chat loop ------------------------------------------------------------

def chat(user_input: str) -> str:
    """Minimal one-turn-with-tools loop. In production, wrap in a multi-turn agent."""
    from openai import OpenAI

    client = OpenAI()  # reads OPENAI_API_KEY
    messages = [
        {"role": "system", "content": (
            "You have persistent memory via MemKraft. Before answering "
            "questions about people or projects, call memkraft_search. "
            "When the user teaches you a fact worth keeping, call "
            "memkraft_remember. Use memkraft_recall for full dossiers."
        )},
        {"role": "user", "content": user_input},
    ]

    while True:
        resp = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=messages,
            tools=TOOLS,
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            return msg.content or ""

        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            result = dispatch(call.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, default=str),
            })


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Who is Simon Kim?"
    print(chat(q))
