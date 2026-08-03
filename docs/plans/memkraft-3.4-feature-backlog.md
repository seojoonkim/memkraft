# MemKraft 3.4 Evidence-Gated Feature Backlog

This backlog is not a release commitment. MemKraft 3.3.0 development proceeds independently with the locked MKEP/0 scope. An item moves from backlog to a 3.4 implementation plan only after its promotion evidence is reproduced and its compatibility cost is reviewed.

## Promotion rules

1. A convenience improvement, fewer adapter lookups, or speculative future use is not sufficient evidence.
2. Every promoted item needs a failing test, benchmark, security requirement, or two-runtime trace that demonstrates a correctness or operational failure in 3.3.0.
3. The proposal must preserve 3.2.x public contracts and keep execution-schema-1 records readable.
4. Prefer adapter, provenance-query, or documentation improvements over new core records or wire fields.
5. Each item is independently accepted, rejected, or retained; the `3.4` label does not imply automatic inclusion.

## Candidates

### Projection cache
- Current default: absent; projections replay from the append-only log.
- Promotion evidence: measured failure of 3.3 gate G11 on the specified corpus.
- Required proof: deleting the cache yields byte-identical output.

### Verified authorization evidence
- Current default: `authority_verified` is always false; gates are advisory.
- Promotion evidence: a concrete verification scheme, trust root, and caller requirement.
- Required proof: threat-model tests reject forged and unsupported schemes.

### Per-recipient origin identifiers
- Current default: stable origin UUID with documented linkability.
- Promotion evidence: a concrete cross-recipient unlinkability requirement.
- Required proof: deterministic recipient-scoped derivation and key-management design.

### Signed handoff envelopes
- Current default: payload digest proves self-consistency, not authenticity.
- Promotion evidence: an authenticated cross-base handoff use case.
- Required proof: key distribution, rotation, verification failure, and replay behavior.

### MCP mutation
- Current default: MCP projection is read-only.
- Promotion evidence: a model-facing mutation use case that cannot safely use the typed Python or CLI adapter.
- Required proof: per-client base selection and adapter-mediated receipt provenance.

### Execution-log compaction and retention
- Current default: execution logs are not compacted and disappear only through explicit forget.
- Promotion evidence: measured storage or replay failure under a realistic corpus.
- Required proof: event-sequence safety, deterministic replay, crash recovery, and retention semantics.

### Raw graph-correlation fields
- Candidate fields: step, attempt, ordered multi-parent, or runtime-checkpoint references.
- Current default: use execution lineage, binding digest, receipt provenance, and typed handoff payload.
- Promotion evidence: correctness failures from at least two runtimes with materially different replay models.
- Non-evidence: shorter payloads, easier debugging, or one fewer provenance lookup.
- Required proof: the proposal does not turn the binding into an adjacency list or make core validate graph topology.