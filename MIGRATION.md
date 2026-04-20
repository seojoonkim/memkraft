# Migrating to MemKraft 1.0.0

> **TL;DR**: No breaking changes. `pip install --upgrade memkraft` and your 0.9.x code keeps working.

---

## From 0.9.x → 1.0.0

### What's new in 1.0.0

MemKraft 1.0.0 is an **integration release**. Every primitive from 0.5 → 0.9 now composes into a complete self-improvement loop for AI agents.

**Philosophy**: *Bitemporal memory × empirical tuning: the first self-improvement ledger for AI agents.*

### New APIs (additive only)

| API | Purpose |
|-----|---------|
| `prompt_register` | Register any prompt/skill as a tracked entity with tier + metadata |
| `prompt_eval` | Record one empirical tuning iteration (scenarios + results) as a decision + incident |
| `prompt_evidence` | Cite past tuning results via bitemporal decision search |
| `convergence_check` | Auto-judge mizchi-style convergence with decay-weighted scoring |

### Breaking changes

**None.** All 0.9.x public APIs keep their exact signatures and behavior.

- ✅ `track`, `update`, `search` — unchanged
- ✅ `tier_set`, `fact_add`, `log_event` — unchanged
- ✅ `decision_record`, `evidence_first` — unchanged
- ✅ Storage layout (`memory/` directory) — unchanged
- ✅ No new required dependencies (stays zero-dep for core)

### Deprecations

None in 1.0.0.

---

## Recommended upgrade path

```bash
pip install --upgrade memkraft
# or, if installed via pipx
pipx upgrade memkraft
```

Verify:

```python
import memkraft
print(memkraft.__version__)  # 1.0.0
```

Existing `memory/` directories need zero migration. Open and keep working.

---

## New capabilities at a glance

### 1. Register a skill/prompt as a tracked entity

```python
from memkraft import MemKraft
mk = MemKraft("./memory")

mk.prompt_register(
    "my-skill",
    source_path="./skills/my-skill.md",
    owner="zeon",
    tier="core",
)
```

### 2. Record an empirical tuning iteration

```python
mk.prompt_eval(
    "my-skill",
    iteration=1,
    scenarios=[
        {"id": "s1", "input": "...", "expected": "..."},
        {"id": "s2", "input": "...", "expected": "..."},
    ],
    results=[
        {"id": "s1", "passed": True, "notes": "✓"},
        {"id": "s2", "passed": False, "notes": "edge case"},
    ],
    changed_by="zeon",
)
```

Each call is stored as a bitemporal **decision** + optional **incident** (on regression) — so every iteration is auditable and time-travelable.

### 3. Cite past tuning results

```python
evidence = mk.prompt_evidence(
    "my-skill",
    query="accuracy improvement on edge cases",
    limit=5,
)
for e in evidence:
    print(e["iteration"], e["pass_rate"], e["excerpt"])
```

Uses bitemporal decision search under the hood — you get the **as-of-then** view, not a rewritten summary.

### 4. Auto-judge convergence

```python
result = mk.convergence_check("my-skill")
print(result["converged"])       # True / False
print(result["score"])           # decay-weighted pass-rate trend
print(result["recommendation"])  # "continue" | "ship" | "rollback"
```

Mizchi-style: recent iterations weigh more via exponential decay, so one bad old run doesn't poison a converged skill.

---

## Full loop (1.0.0 self-improvement ledger)

```python
from memkraft import MemKraft
mk = MemKraft("./memory")

# 1. Register
mk.prompt_register("my-skill", source_path="./skills/my-skill.md", owner="zeon")

# 2. Run iterations over time
for i in range(1, 6):
    mk.prompt_eval("my-skill", iteration=i, scenarios=[...], results=[...])

# 3. Before next iteration, recall what already worked
evidence = mk.prompt_evidence("my-skill", query="what improved accuracy")

# 4. Decide whether to keep tuning or ship
decision = mk.convergence_check("my-skill")
if decision["converged"]:
    print("🚢 Ship it.")
else:
    print("🔁 Keep iterating:", decision["recommendation"])
```

Every step above is a plain-Markdown artifact under `memory/`. No LLM calls inside MemKraft. No vector DB required. No cloud.

---

## From pre-0.9.x

Upgrade to 0.9.x first (see previous `CHANGELOG.md` entries for 0.5 → 0.9 migration notes), then follow this guide. The 0.9 → 1.0 step is a pure version bump.

---

## Questions

- GitHub Issues: https://github.com/seojoonkim/memkraft/issues
- Changelog: [CHANGELOG.md](./CHANGELOG.md)
