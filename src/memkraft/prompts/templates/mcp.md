# MemKraft MCP server

MemKraft v{VERSION} ships a built-in MCP stdio server.

## Install

```bash
pip install "memkraft[mcp]"
```

## claude_desktop_config.json snippet

```json
{
  "mcpServers": {
    "memkraft": {
      "command": "python",
      "args": ["-m", "memkraft.mcp"],
      "env": {
        "MEMKRAFT_DIR": "{BASE_DIR}"
      }
    }
  }
}
```

## Exposed tools

- `remember(name, info, source?)` — accumulate info on an entity.
- `search(query, fuzzy?)` — hybrid memory search.
- `recall(name)` — full dossier (compiled truth + timeline).
- `link(source, target)` — create a wiki-style link between entities.

## Smoke test

```bash
MEMKRAFT_DIR="{BASE_DIR}" python -m memkraft.mcp --help
```

If `mcp` is not installed you'll see a clear hint to `pip install "memkraft[mcp]"`.
