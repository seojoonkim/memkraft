# MemKraft + OpenAI Function Calling

Expose MemKraft as OpenAI function calls. Copy-paste both the **schema** and the **dispatcher** below.

Base dir: `{BASE_DIR}`

## Function schemas (v{VERSION})

```python
MEMKRAFT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memkraft_remember",
            "description": "Store new information about an entity (person/org/project).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Entity name."},
                    "info": {"type": "string", "description": "New info to record."},
                    "source": {"type": "string", "description": "Source attribution.", "default": "chat"},
                },
                "required": ["name", "info"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memkraft_search",
            "description": "Hybrid search across all stored memory (exact + IDF + fuzzy).",
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
            "description": "Fetch a specific entity's compiled state and recent timeline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
]
```

## Dispatcher

```python
from memkraft import MemKraft

mk = MemKraft(base_dir="{BASE_DIR}")

def dispatch(name: str, args: dict):
    if name == "memkraft_remember":
        mk.update(args["name"], args["info"], source=args.get("source", "chat"))
        return {"ok": True}
    if name == "memkraft_search":
        return mk.search(args["query"], fuzzy=args.get("fuzzy", True))
    if name == "memkraft_recall":
        return mk.brief(args["name"])
    raise ValueError(f"unknown tool: {name}")
```

## Custom GPT Instructions (paste-in)

> You have access to a persistent memory system called MemKraft. Before answering
> any question about people, projects, or past decisions, call `memkraft_search`.
> When the user tells you something worth remembering (new facts, decisions,
> preferences), call `memkraft_remember`. To get a full dossier on a known entity,
> call `memkraft_recall`.
>
> Do not invent entities. If search returns nothing, say so.
