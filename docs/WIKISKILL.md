# Deterministic WikiSkill-style experience pipeline

These additive Python APIs do not change candidates, ReasoningBank, search, or
existing provider behavior. They store inert JSONL records, not executable skills.

```python
from memkraft import MemKraft

mk = MemKraft("./memory")
experience = mk.experience_append(
    "Retry only transient failures; stop after three attempts.",
    provenance_id="session-42/tool-result-8",
    session_id="session-42",
    verification={
        "status": "verified",
        "verified_by": "pytest tests/test_retry.py",
        "evidence": ["run-42:exit-0"],
    },
)
wiki = mk.experience_promote(experience["id"], title="Bounded retries")
skill = mk.skill_compile("bounded-retry", wiki_ids=[wiki["id"]])
assert skill["steps"] == [experience["text"]]
```

## Contracts

- `experience_append(text, *, provenance_id, session_id=None, verification=None)`
  returns the complete appended record. Exact text and provenance identifiers are
  preserved. Verification defaults to `{"status": "unverified"}`; supported
  statuses are `unverified`, `failed`, and `verified`. A verified attestation must
  contain a nonempty `verified_by` string and a nonempty list of evidence-reference
  strings. Additional JSON-serializable metadata is retained.
- `experience_promote(experience_id, *, title)` loads a unique, visible, verified
  raw experience from disk and copies its exact text, provenance and verification
  into wiki knowledge with an `experience_id` backlink. It does not edit the raw
  record or invent a summary. Missing/deleted sources raise `KeyError`; invalid
  records/verification raise `ValueError` before writing the target.
- `skill_compile(name, *, wiki_ids)` requires a nonempty list/tuple of stored,
  visible, verified wiki IDs. It removes duplicate IDs in first-seen order and
  copies each wiki's exact text into `steps`, retaining aligned `wiki_ids`,
  `experience_ids`, `provenance_ids`, and `source_verifications`. It never accepts
  raw experience IDs as wiki IDs. Every input is checked before the skill append.
- Sidecars under `MemKraft.base_dir/.memkraft/`: `experiences.jsonl`, `wiki.jsonl`,
  `skills.jsonl`. Writes use `store_core.append` (locked JSONL append and v1
  envelope); reads use `store_core.read_all` (existing tombstone filtering).
- Repeated calls append separate records; generated IDs and timestamps vary, but
  compiled content is deterministic. There is no automatic deduplication between
  calls, no mutation of prior attestations, and no migration of existing stores.

## Explicit trust and lifecycle boundaries

Verification is a **caller attestation**. This module does not execute the named
verifier, dereference evidence or provenance references, verify signatures, or
prove that evidence supports the text. A trusted caller must supply actual review
or test evidence. Do not mark model claims verified without independent evidence.
Raw text is preserved, not redacted: callers must remove secrets before capture.

An initially unverified record cannot be upgraded in place in this minimal API;
append a new independently verified experience with its provenance and promote
that record. Derived records are snapshots: later source deletion/revocation is
not cascaded. Per-file appends are locked, but cross-file reads and appends are not
a multi-store transaction; concurrent source revocation is outside this contract.
Only sequential tombstone exclusion is guaranteed for source selection.

No LLM extraction, automatic promotion, generalized procedural synthesis,
SKILL.md installation, host integration, CLI/MCP exposure, global retrieval,
retention/forget cascade, or automatic skill execution is included. Skills remain
inert records requiring an explicit downstream consumer. Direct local store
editing is trusted and is not protected by a cryptographic authenticity boundary.
