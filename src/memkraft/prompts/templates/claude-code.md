<!-- MEMKRAFT-BLOCK-START (v{VERSION}) -->
## 🧠 MemKraft — Memory API first

MemKraft v{VERSION} is installed. **Before editing memory/* files by hand, try the Python API first.**

Base dir: `{BASE_DIR}`

### 6 core calls

```python
from memkraft import MemKraft
mk = MemKraft(base_dir="{BASE_DIR}")

mk.track("Hong Gildong", entity_type="person", source="DM")      # start tracking
mk.update("Hong Gildong", "joined Hashed as CTO", source="press") # accumulate info
mk.search("Hashed CEO")                                           # hybrid search
mk.tier_set("hong-gildong", tier="core")                          # core / recall / archival
mk.fact_add("Simon", "role", "CEO", valid_from="2020-03-01")      # bitemporal fact
mk.log_event("vibekai deploy done", tags="deploy", importance="high")
```

### Gotchas

- **Tier values are `core` / `recall` / `archival` only** (`critical` ❌)
- `decay_rate` is in (0, 1) exclusive — not `weight`
- `promote()` (markdown tag) ≠ `tier_set()` (frontmatter). Prefer `tier_set`.
- For past memory lookups → try `mk.search(...)` before `grep`
- After editing `[[wiki-links]]`, call `mk.link_scan()`

### When to use the API vs write files

- ✅ API first: people/orgs/projects, time-scoped facts, deploy/decision events, search
- 📝 Direct edit OK: long essays, freeform daily logs, verbatim quotes (`originals/`)

Triggers: `memory`, `remember`, `recall`, `memkraft`, `mk`, `bitemporal`, `decay`, `tier`, `entity`
<!-- MEMKRAFT-BLOCK-END -->
