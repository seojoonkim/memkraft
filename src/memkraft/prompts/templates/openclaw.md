<!-- MK-ADOPTION-BLOCK-START (v{VERSION}, auto-managed) -->
## 🧠 MemKraft API First Principle — TIER 1

**MemKraft v{VERSION}** is installed (Bitemporal + Tier + Decay + Link Graph).
**Before editing files by hand, check the API.**

Base dir: `{BASE_DIR}`

### 6 basic calls
```python
from memkraft import MemKraft
mk = MemKraft(base_dir="{BASE_DIR}")

mk.track("Hong Gildong", entity_type="person", source="DM")
mk.update("Hong Gildong", "joined Hashed as CTO", source="press")
mk.search("Hashed CEO")                                       # hybrid: exact+IDF+fuzzy
mk.tier_set("hong-gildong", tier="core")                      # core / recall / archival
mk.fact_add("Simon", "role", "CEO", valid_from="2020-03-01")
mk.log_event("vibekai deploy done", tags="deploy", importance="high")
```

### ⚠️ Common mistakes
- **Tier values: `core` / `recall` / `archival` only** (`critical` ❌)
- **`decay_rate` (NOT `weight`), range (0, 1) exclusive**
- `promote()` (markdown tag) ≠ `tier_set()` (frontmatter). **Official in v0.8+: `tier_set`**
- For past memory: `mk.search` first, then `grep` if needed
- After editing `[[wiki-links]]`: call `mk.link_scan()`

### When API / when file edit
- ✅ API first: person/org/project entities, time-scoped facts, deploy/decision events, search
- 📝 File edit OK: long essay/plan drafts, daily log freeform, verbatim originals (`originals/`)

**Skill triggers:** `memory`, `remember`, `recall`, `memkraft`, `mk`, `bitemporal`, `decay`, `tier`, `entity`

<!-- MK-ADOPTION-BLOCK-END -->
