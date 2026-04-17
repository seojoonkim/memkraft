<!-- MEMKRAFT-BLOCK-START (drop-in for Claude Code / AGENTS.md) -->
## 🧠 MemKraft — Memory API first

MemKraft is installed locally. **Before editing `memory/*` files by hand, try the Python API first.**

### 6 core calls

```python
from memkraft import MemKraft
mk = MemKraft()  # respects MEMKRAFT_DIR env, falls back to ./memory

mk.track("Hong Gildong", entity_type="person", source="DM")
mk.update("Hong Gildong", "joined Hashed as CTO", source="press")
mk.search("Hashed CEO")
mk.tier_set("hong-gildong", tier="core")          # core / recall / archival
mk.fact_add("Simon", "role", "CEO", valid_from="2020-03-01")
mk.log_event("vibekai deploy done", tags="deploy", importance="high")
```

### Gotchas

- Tier values are `core` / `recall` / `archival` **only** (`critical` ❌).
- `decay_rate` is in (0, 1) exclusive — not `weight`.
- `promote()` (markdown tag) ≠ `tier_set()` (frontmatter). Prefer `tier_set`.
- Past memory lookup → `mk.search(...)` before `grep`.
- After editing `[[wiki-links]]`, call `mk.link_scan()`.

### Regenerate this block

```bash
memkraft agents-hint claude-code > AGENTS.md  # or paste into an existing file
```
<!-- MEMKRAFT-BLOCK-END -->
