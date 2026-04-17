# MemKraft integration (v{VERSION})

You have a local memory system: MemKraft at `{BASE_DIR}`.

## When to use it

- Before claiming a fact about any person, company, or project → search first.
- When the user teaches you something worth remembering (decisions, preferences,
  new relationships) → record it.
- When editing code that touches a user's ongoing project → recall context first.

## Use the Python API, not hand-edits

```python
from memkraft import MemKraft
mk = MemKraft(base_dir="{BASE_DIR}")

mk.search("payments refactor")
mk.update("payments-service", "migrated to Stripe billing portal", source="chat")
mk.log_event("deploy succeeded: payments v2.3", tags="deploy", importance="high")
```

## Quick rules

- Tier values: `core` / `recall` / `archival` only.
- `decay_rate` is in (0, 1). Do not pass `weight`.
- After editing `[[wiki-links]]`, call `mk.link_scan()`.
- For long-form memos/essays, write a file under `{BASE_DIR}/` directly.
- For structured facts, use the API (it handles bitemporal validity).

## CLI fallback

```bash
memkraft search "payments refactor"
memkraft update "payments-service" --info "migrated to Stripe billing portal"
memkraft brief "payments-service"
```
