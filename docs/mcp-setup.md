# MCP Setup — Claude Desktop + Cursor

MemKraft ships an MCP (Model Context Protocol) server that exposes the four core primitives as tools:

- `remember(name, info, source)` — append to an entity timeline
- `search(query, fuzzy)` — hybrid search (exact + IDF + fuzzy)
- `recall(name)` — read an entity's compiled-truth + timeline
- `link(source, target)` — add a wiki-link between entities

---

## 1. Install

```bash
pip install 'memkraft[mcp]'
```

Verify:

```bash
memkraft mcp doctor     # checks install + entry point + Claude Desktop config
memkraft mcp test       # remember → search → recall round-trip in a temp workspace
```

If `mcp doctor` reports 🟡 `mcp package NOT installed`, re-run the install above (you may have installed bare `memkraft` without the extra).

---

## 2. Claude Desktop

### Locate the config

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json` (varies by distribution)

If the file does not exist yet, create it.

### Merge this snippet

```json
{
  "mcpServers": {
    "memkraft": {
      "command": "python3",
      "args": ["-m", "memkraft.mcp"],
      "env": {
        "MEMKRAFT_DIR": "/ABSOLUTE/PATH/TO/YOUR/memory"
      }
    }
  }
}
```

Replace `/ABSOLUTE/PATH/TO/YOUR/memory` with the absolute path where you want MemKraft to store its files. A common choice is `~/memory`.

> **Tip:** Run `memkraft init --template mcp` in a new project and it will generate both a `memory/` directory and a pre-filled `claude_desktop_config.snippet.json` for copy-paste.

### Restart Claude Desktop

Quit and relaunch. You should see a hammer icon in the input bar; clicking it shows `memkraft` among the available tools.

### Sanity check

Ask Claude:

> Use the memkraft `remember` tool to store: name = "Demo", info = "MCP is wired up".

Then:

> Use memkraft `recall` on "Demo".

If you see the text you just stored, the round-trip works.

---

## 3. Cursor

Cursor's MCP support uses the same stdio protocol. Add the server under `~/.cursor/mcp.json` (or the equivalent location shown in Cursor's Settings → MCP pane):

```json
{
  "mcpServers": {
    "memkraft": {
      "command": "python3",
      "args": ["-m", "memkraft.mcp"],
      "env": {
        "MEMKRAFT_DIR": "/ABSOLUTE/PATH/TO/YOUR/memory"
      }
    }
  }
}
```

Then restart Cursor.

---

## 4. Troubleshooting

### `mcp package NOT installed`
You installed `memkraft` without the MCP extra. Fix:
```bash
pip install 'memkraft[mcp]'
```

### `python3: command not found` (inside Claude Desktop)
Claude Desktop launches the server from its own minimal `PATH`. Use an **absolute** Python path in the config:
```json
"command": "/usr/local/bin/python3"
```
Find yours with:
```bash
which python3
```

### `MEMKRAFT_DIR` path does not exist
Create it first:
```bash
mkdir -p ~/memory
memkraft init --path ~
```

### Tools don't appear in Claude Desktop
1. Confirm the JSON is valid (run it through `jq . < claude_desktop_config.json`).
2. Fully quit Claude Desktop (Cmd+Q on macOS — closing the window is not enough).
3. Check Claude Desktop logs:
   - macOS: `~/Library/Logs/Claude/`
   - Windows: `%APPDATA%\Claude\logs\`

### `memkraft mcp test` reports `search: 0 result(s)`
The search index may not have picked up the new entry yet. Run:
```bash
memkraft index
```
Then retry.

### Permission errors on `MEMKRAFT_DIR`
Claude Desktop runs the server under your user account, so the directory must be user-writable. Check:
```bash
ls -ld "$MEMKRAFT_DIR"
```

---

## 5. Advanced — running the server manually

For debugging, you can run the server in a terminal and send it stdio directly:

```bash
MEMKRAFT_DIR=~/memory python3 -m memkraft.mcp
```

It will wait for MCP handshake frames on stdin.

To get a structured manifest (useful for wrapping in other tools), use:
```bash
memkraft agents-hint mcp --format json
```
This prints a JSON envelope with the full Claude Desktop snippet you can pipe into `jq`.

---

## See also

- [`memkraft doctor --fix`](../README.md#doctor) — auto-repair missing memory structure
- [`memkraft stats --export json`](../README.md#stats) — dashboard export
- [`memkraft init --template <name>`](../README.md#init-templates) — project scaffolding
