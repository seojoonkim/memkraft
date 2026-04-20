# CHANGELOG

## [1.0.0] — 2026-04-21

### Philosophy

> Bitemporal memory × empirical tuning: the first self-improvement ledger for AI agents.

Your agent's accountable past, in plain Markdown.

### New in 1.0.0

- **`prompt_register`** — Register any prompt/skill as a tracked entity with tier + metadata.
- **`prompt_eval`** — Record one empirical tuning iteration (scenarios + results) as a bitemporal decision + incident (on regression).
- **`prompt_evidence`** — Cite past tuning results via bitemporal decision search. As-of-then view, not a rewrite.
- **`convergence_check`** — Auto-judge mizchi-style convergence with decay-weighted pass-rate trend.

Together these four APIs close the loop that began in 0.5: **register → tune → recall → decide**, every step auditable and time-travelable in plain Markdown.

### Upgrade from 0.9.x

**Zero breaking changes.** All 0.9.x public APIs keep their exact signatures. Storage layout unchanged. See [MIGRATION.md](./MIGRATION.md).

```bash
pip install --upgrade memkraft
```

### Architecture

1.0.0 is an **integration release**, not a new-backend release.

- ✅ No new storage engines. Pure Markdown + frontmatter, as always.
- ✅ No new required dependencies. Core stays zero-dep.
- ✅ No LLM calls inside MemKraft. The ledger is data; the agent is the LLM.
- ✅ Every 0.9.x primitive now composes into the full self-improvement loop.

### API surface (total: 12 public methods)

| API | Since | Role |
|-----|-------|------|
| `track` | 0.5 | Start tracking an entity |
| `update` | 0.5 | Append information to an entity |
| `search` | 0.5 | Hybrid search (exact + IDF + fuzzy) |
| `tier_set` | 0.8 | Set tier: `core` / `recall` / `archival` |
| `fact_add` | 0.8 | Record a bitemporal fact |
| `log_event` | 0.8 | Log a timestamped event |
| `decision_record` | 0.9 | Capture a decision with rationale |
| `evidence_first` | 0.9 | Retrieve evidence before acting |
| `prompt_register` | **1.0** | Register a prompt/skill as an entity |
| `prompt_eval` | **1.0** | Record one tuning iteration |
| `prompt_evidence` | **1.0** | Cite past tuning results |
| `convergence_check` | **1.0** | Auto-judge convergence |

### Tests

731 passed, 1 skipped (same as 0.9.2a2 — 1.0.0 adds zero new test code; it's a stabilization + docs release).

### Deprecations

None.

---

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
