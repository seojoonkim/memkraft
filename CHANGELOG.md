# CHANGELOG

## [0.8.4] - 2026-04-18

### Fixed
- `brief()` now returns text from MCP recall path (critical bug: existing entities reported as `found: False`) (#3)
- `track()` correctly returns `Path` matching `Optional[Path]` signature (#2)

### Improved
- Korean josa (조사) stripping with longest-match for complex particles (`에서`, `한테서`, `로서`) + 2-char guard for names like "이은" (#1)

### Tests
- 594 passed, 1 skipped (+21 from 0.8.3)

---

## [0.8.3] - 2026-04-17

### Added
- **`memkraft init --template <name>`** — 5 presets: `claude-code`, `cursor`, `mcp`, `minimal`, `rag`. Idempotent: existing files are preserved on re-run.
- **`memkraft templates list`** — browse available scaffolding templates.
- **`memkraft agents-hint --format json`** — structured output for CI/automation. All 6 targets emit a stable envelope with `{target, version, base_dir, content}`.
- **`memkraft doctor --fix [--dry-run] [--yes]`** — auto-repair missing `memory/` structure. **Create-only, never deletes.** Prompts for confirmation unless `--yes` or `--dry-run`.
- **`memkraft stats`** — workspace dashboard. `--export json|csv` + `--out <path>` for CI.
- **`memkraft mcp doctor`** / **`memkraft mcp test`** — production validation for the MCP server. `doctor` checks extras + entry point + tool schemas + Claude Desktop config location. `test` runs a remember→search→recall round-trip in a temp workspace.
- **`docs/mcp-setup.md`** — Claude Desktop + Cursor integration guide, including troubleshooting.

### Changed
- `pyproject.toml` gains richer `project.urls` (Documentation, Bug Tracker, Changelog).
- New package data: `templates_pkg/*.json` shipped in the wheel.

### Compatibility
- Fully backward-compatible with 0.8.2. All 538 existing tests still pass; 35 new tests added (573 total).

---

## [0.8.2] - 2026-04-17

### Added
- **`memkraft selfupdate`** — self-upgrade via pip when a newer release is on PyPI. `--dry-run` to check only.
- **`memkraft doctor --check-updates`** — doctor now optionally checks PyPI and reports 🟢 up-to-date / 🟡 update available / 🔴 PyPI unreachable.
- **GitHub Actions auto-release** (`.github/workflows/release.yml`) — push a `vX.Y.Z` tag and CI builds, verifies, uploads to PyPI, and cuts a GitHub Release.

### Docs
- README: new "Staying up to date" section.
- Maintainers: PyPI publishes require `PYPI_API_TOKEN` repo secret.

### Compatibility
- Fully backward-compatible with 0.8.1. No public API changes; all 515 existing tests pass.

---

## [0.8.1] - 2026-04-17

### Added
- **`mk.init()` now returns `{"created": [...], "exists": [...], "base_dir": "..."}`** — quickstart examples actually work.
- **`memkraft agents-hint <target>` CLI** — copy-paste integration snippets for 6 targets: `claude-code`, `openclaw`, `openai`, `cursor`, `mcp`, `langchain`. Also supports `--format json` and `--base-dir` overrides.
- **`examples/` folder** — drop-in AGENTS.md block, OpenAI function-calling example, 10-line RAG loop.
- **`python -m memkraft.mcp`** — MCP stdio server exposing `remember`, `search`, `recall`, `link`. Requires `pip install 'memkraft[mcp]'`.
- **`memkraft watch`** — filesystem auto-reindex. Requires `pip install 'memkraft[watch]'`.
- **`memkraft doctor`** — health check for install + memory structure, with 🟢/🟡/🔴 icons and suggested fixes.

### Fixed
- README Quick Start example using `mk.init()` previously produced no observable side effect besides printing; now returns a structured dict so tests and scripts can branch on it.

### Changed
- `pyproject.toml` gains `[project.optional-dependencies]`: `mcp`, `watch`, and `all`.
- Package now ships `src/memkraft/prompts/templates/*.md` as package-data and includes `examples/` in sdist via `MANIFEST.in`.

### Compatibility
- Fully backward-compatible with 0.8.0. All 492 existing tests still pass; 23 new tests added (515 total).

---

## [0.8.0] - 2026-04-16

Bitemporal validity, tier system (core/recall/archival), decay mechanics, wiki-link graph. See README for details.

---

## Older versions

See README §Changelog for v0.7.x and earlier.
