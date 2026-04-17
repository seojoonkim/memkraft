# Example Claude Code session

A real interaction showing Claude Code using MemKraft as persistent memory.

---

**User:** Remember that Simon Kim joined Hashed as CEO in 2020.

**Claude Code:** I'll record that with MemKraft.

```python
from memkraft import MemKraft
mk = MemKraft()
mk.track("Simon Kim", entity_type="person", source="user DM")
mk.fact_add("Simon Kim", "role", "CEO of Hashed", valid_from="2020-01-01")
mk.update("Simon Kim", "CEO of Hashed since 2020", source="user DM")
```

Done. Simon Kim is now tracked with a bitemporal fact (role=CEO, valid_from=2020-01-01).

---

**User:** Who's Simon?

**Claude Code:** Let me check memory first.

```python
mk.search("Simon")
# → [{"name": "Simon Kim", "excerpt": "CEO of Hashed since 2020", ...}]

mk.brief("Simon Kim")
# → full compiled dossier
```

Simon Kim is tracked as CEO of Hashed (since 2020-01-01). I have one source ("user DM") so take the rest with a grain of salt — want me to enrich via a news search?

---

**User:** Yes, and log that I'm thinking about investing in his new fund.

**Claude Code:**

```python
mk.log_event(
    "Simon considering investment in Hashed's new fund",
    tags="investment,decision-pending",
    importance="high",
)
mk.update("Simon Kim", "new fund in formation as of 2026-04", source="user DM")
```

Logged as a decision-pending high-importance event.

---

### Why this works

- MemKraft lets Claude Code **accumulate** facts instead of overwriting them.
- `fact_add` carries bitemporal validity — you can answer "who was CEO in March 2020?" correctly even after leadership changes.
- `log_event` creates an auditable timeline separate from entity pages.
- Every call is pure filesystem writes under your `MEMKRAFT_DIR`. No daemon, no DB, no API key.
