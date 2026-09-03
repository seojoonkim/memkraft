"""MemKraft — The compound knowledge system for AI agents"""

__version__ = "4.0.2"

from .core import MemKraft as _BaseMemKraft
from .bitemporal import BitemporalMixin
from .decay import DecayMixin
from .links import LinksMixin
from .tiers import TiersMixin
from .incident import IncidentMixin
from .runbook import RunbookMixin
from .rca import RCAMixin
from .decision_store import DecisionStoreMixin
from .prompt_tune import PromptTuneMixin  # v0.9.2 M1 alpha
from .prompt_evidence import PromptEvidenceMixin  # v0.9.2 M2 alpha
from .convergence import ConvergenceMixin  # v0.9.2 M2 alpha
from .search import SearchMixin  # v1.0.2 search enhancements
from .chunking import ChunkingMixin  # v1.0.3 chunking + precision search
from .lifecycle import LifecycleMixin  # v1.1.0 autonomous memory management
from .graph import GraphMixin  # v2.0.0 SQLite graph layer
from .preference_graph_sync import PreferenceGraphSyncMixin  # v2.11 preference → graph bridge
from .multimodal import MultimodalMixin  # v2.1 multimodal attachments
from .multi_pass import MultiPassMixin  # v2.2 multi-pass retrieval
from .routing import RoutingMixin  # v2.2 question-type routing
from .rrf import RRFMixin  # v2.3 reciprocal rank fusion
from .consolidation import ConsolidationMixin  # v2.3 sleep consolidation
from .temporal_chain import TemporalChainMixin  # v2.3+ multi-session temporal chain
from .confidence import (  # v2.4 confidence + implicit-acquisition
    ConfidenceMixin,
    install_confidence_wrappers,
)
from .context_compress import ContextCompressMixin  # v2.5 context compression
from .rerank import RerankMixin  # v2.5 question-type-aware re-ranking
from .hierarchical import HierarchicalMixin  # v1.1.2 hierarchical retrieval
from .alias import AliasMixin  # v2.4 entity alias support
from .cache import (  # v2.7.0 search result caching
    CacheInvalidationMixin,
    install_cache_invalidation_wrappers,
)
from .reasoning_bank import ReasoningBankMixin  # v2.7.1 ReasoningBank
from .reasoning_execution import ReasoningAuthorization, ReasoningExecutionResult
from .struct_mem import StructMemMixin  # v2.9.2 StructMem regex extraction
from .embedding import EmbeddingMixin  # v2.7.3 local embedding retrieval
from .provenance import ProvenanceMixin  # v3 M2 Provenance Core
from .live_sync import LiveSyncMixin  # v3.2 local-first live sync
from .candidates import CandidateMixin  # v2.13 candidate memory preview
from .interactions import LastInteractionMixin  # v2.13 last-interaction preview
from .derived_views import DerivedViewsMixin  # v2.14 canonical truth preview
from .context_compiler import ContextCompilerMixin  # v2.15 A1 context compiler preview
from .evidence_context import EvidenceContextMixin  # query-focused evidence compiler
from .numeric_aggregation import NumericAggregationMixin  # safe numeric evidence composition
from .execution_state import ExecutionStateMixin  # v3.3 MKEP/0 execution kernel
from .execution_handoff import ExecutionHandoffMixin  # v3.3 MKEP/0 typed handoff
from .evaluation_corpus import EvaluationCorpusMixin  # v3.3 evaluation corpus
from .improvement_ledger import ImprovementLedgerMixin  # continual improvement ledger preview
from .correction_policy import CorrectionPolicyMixin  # correction learning policy preview
from .delay_ledger import DelayLedgerMixin  # timing and delay evidence preview
from .state_contract import StateContractMixin  # v3.3 project-state contract
from .project_memory import ProjectMemoryMixin  # v3.5 project memory compiler preview
from .artifacts import ArtifactMixin  # structured completed-content artifacts
from .memory_graph import MemoryGraphMixin  # strict append-only exact memory graph
from .focus import FocusMixin  # v3.8 TTL-bounded inert current focus
from .authority import AuthorityMixin  # v3.8 decision authority provenance
from .self_evolving import SelfEvolvingMixin  # v4.0 self-evolving substrate
from .development_experience import DevelopmentExperienceMixin
from .adapter import MemoryAdapter  # v4.0 transport-neutral agent contract
from .outcomes import OutcomeLoopMixin  # v2.15 A2 outcome loop preview
from .preference import PreferenceMixin  # v2.7.2 — selectively attached below
# Note: PreferenceMixin is NOT added to the global mixin loop because its
# `_slugify` would clobber core._slugify (which has Korean/CJK support).
# Instead, we attach only the public preference methods further down.


# v0.8.0: extend MemKraft in-place with new mixins so every existing
# ``from memkraft import MemKraft`` continues to work unchanged.  The
# mixin methods are added to the class object itself — we don't subclass
# because existing call sites (and tests) do ``MemKraft(...)``.
# v0.9.0: incident/runbook/rca mixins added here (additive, no breaking
# changes).
# v0.9.2 M1: prompt_tune mixin added here (additive; implements
# ``prompt_register`` + ``prompt_eval`` on top of decision_store +
# incident + tier + link primitives).
# v0.9.2 M2: prompt_evidence + convergence_check mixins — additive;
# built on decision_search + decision_get + tier.
#
# Legacy mixins intentionally extend/override APIs installed earlier in this
# ordered composition (for example LifecycleMixin.health replaces core's old
# health_check alias).  The two new composition helpers have a stricter
# additive-only contract: they may add APIs, but never replace existing ones.
_ADDITIVE_ONLY_MIXINS = (
    EvidenceContextMixin,
    NumericAggregationMixin,
    ExecutionStateMixin,
    ExecutionHandoffMixin,
    EvaluationCorpusMixin,
    ImprovementLedgerMixin,
    CorrectionPolicyMixin,
    DelayLedgerMixin,
    StateContractMixin,
    ProjectMemoryMixin,
    ArtifactMixin,
    MemoryGraphMixin,
    FocusMixin,
    AuthorityMixin,
    SelfEvolvingMixin,
    DevelopmentExperienceMixin,
)

for _mixin in (
    BitemporalMixin,
    DecayMixin,
    LinksMixin,
    TiersMixin,
    IncidentMixin,
    RunbookMixin,
    RCAMixin,
    DecisionStoreMixin,
    PromptTuneMixin,
    PromptEvidenceMixin,
    ConvergenceMixin,
    SearchMixin,
    ChunkingMixin,
    LifecycleMixin,
    GraphMixin,
    PreferenceGraphSyncMixin,
    MultimodalMixin,
    MultiPassMixin,
    RoutingMixin,
    RRFMixin,
    ConsolidationMixin,
    ContextCompressMixin,
    RerankMixin,
    ConfidenceMixin,
    TemporalChainMixin,
    HierarchicalMixin,
    AliasMixin,
    CacheInvalidationMixin,
    ReasoningBankMixin,
    StructMemMixin,
    EmbeddingMixin,
    ProvenanceMixin,
    LiveSyncMixin,
    CandidateMixin,
    LastInteractionMixin,
    DerivedViewsMixin,
    OutcomeLoopMixin,
    ContextCompilerMixin,
    EvidenceContextMixin,
    NumericAggregationMixin,
    ExecutionStateMixin,
    ExecutionHandoffMixin,
    EvaluationCorpusMixin,
    ImprovementLedgerMixin,
    CorrectionPolicyMixin,
    DelayLedgerMixin,
    StateContractMixin,
    ProjectMemoryMixin,
    ArtifactMixin,
    MemoryGraphMixin,
    FocusMixin,
    AuthorityMixin,
    SelfEvolvingMixin,
    DevelopmentExperienceMixin,
):
    for _name, _attr in vars(_mixin).items():
        if _name.startswith("__") and _name.endswith("__"):
            continue
        # Preserve historical ordered override semantics for legacy mixins.
        # Only the new composition helpers are strictly additive-only.
        if _mixin in _ADDITIVE_ONLY_MIXINS and hasattr(_BaseMemKraft, _name):
            continue
        if _name == "__init__":
            continue
        setattr(_BaseMemKraft, _name, _attr)


# v2.4 — wrap public search methods so every result list gains a
# ``confidence`` field.  Must run AFTER all mixins are attached so the
# wrappers see the final method bodies (search_multi from MultiPassMixin,
# search_v2 from SearchMixin, etc.).
# v2.5.0 — pref_conflicts_all (not via PreferenceMixin to avoid _slugify collision)

def _pref_conflicts_all(self) -> list:
    """Detect preference conflicts across ALL entities.

    Scans every preference file and reports cases where the same
    entity has the same preference key mapped to different values.

    Returns:
        list[dict]: Each entry has ``entity``, ``conflict``
        (descriptive string), and ``facts`` (list of the
        conflicting value dicts).
    """
    from pathlib import Path as _Path
    import re as _re

    pref_dir = self.base_dir / "preferences"
    if not pref_dir.exists():
        return []

    # Import preference helpers
    from .preference import _PREF_RE, _SIMPLE_PREF_RE

    def _parse(pref_file):
        content = pref_file.read_text(encoding="utf-8")
        results = []
        for raw_line in content.split("\n"):
            line = raw_line.strip()
            if not line.startswith("- "):
                continue
            stripped_key = line[2:].split(":", 1)[0].strip().lower() if ":" in line[2:] else ""
            if stripped_key == "reason" and results:
                _, _, rest = line.partition(":")
                reason_val = rest.strip()
                if reason_val and not results[-1].get("reason"):
                    results[-1]["reason"] = reason_val
                continue
            m = _PREF_RE.search(line)
            if m:
                key_val = line.split("<!--")[0].strip()
                parts = key_val.split(":", 1)
                if len(parts) == 2:
                    results.append({
                        "key": parts[0].lstrip("- ").strip(),
                        "value": parts[1].strip(),
                        "valid_from": m.group("vfrom") or None,
                        "valid_to": m.group("vto") or None,
                        "strength": float(m.group("strength")),
                    })
                continue
            m = _SIMPLE_PREF_RE.search(line)
            if m:
                key_val = line.split("<!--")[0].strip()
                parts = key_val.split(":", 1)
                if len(parts) == 2:
                    results.append({
                        "key": parts[0].lstrip("- ").strip(),
                        "value": parts[1].strip(),
                        "valid_from": m.group("vfrom") or None,
                        "valid_to": m.group("vto") or None,
                        "strength": 1.0,
                    })
        return results

    all_results = []
    for pref_file in sorted(pref_dir.glob("*.md")):
        entity = pref_file.stem
        prefs = _parse(pref_file)
        by_key: dict = {}
        for p in prefs:
            by_key.setdefault(p["key"], []).append(p)
        for key, key_prefs in by_key.items():
            if len(key_prefs) > 1:
                values = set(p["value"] for p in key_prefs)
                if len(values) > 1:
                    all_results.append({
                        "entity": entity,
                        "conflict": f"{key}: {' vs '.join(values)}",
                        "facts": key_prefs,
                    })
    return all_results


setattr(_BaseMemKraft, "pref_conflicts_all", _pref_conflicts_all)


# v2.7.2 — `pref_conflicts` accepts either zero args (scan all entities,
# legacy v2.5.0 alias behaviour) **or** a single entity name (per-entity
# scan, what `PreferenceMixin.pref_conflicts` shipped with). PersonaMem
# (`src/memkraft/personamem.py:756`) calls `mk.pref_conflicts(name)` and
# previously raised `TypeError`, which the harness's `try/except` swallowed.
def _pref_conflicts(self, entity=None):
    if entity is None:
        return _pref_conflicts_all(self)
    return PreferenceMixin.pref_conflicts(self, entity)


setattr(_BaseMemKraft, "pref_conflicts", _pref_conflicts)

# v2.7.2 — selectively attach PreferenceMixin's public methods so PersonaMem
# (and any other consumer) can actually call pref_set / pref_get /
# pref_context / pref_evolution. We deliberately skip:
#   - _slugify           → core._slugify is more capable (CJK aware)
#   - pref_conflicts*    → already implemented above with cache-aware semantics
# All attached methods rely on `self._slugify`, `self.base_dir`, and the
# regex helpers in `preference.py`, so they cooperate with core's slugger.
_PREF_MIXIN_EXPORTS = (
    "pref_set",
    "pref_get",
    "pref_evolution",
    "pref_context",
    "_close_preference",
    "_parse_preferences",
)
for _name in _PREF_MIXIN_EXPORTS:
    _attr = getattr(PreferenceMixin, _name, None)
    if _attr is None:
        continue
    # Don't clobber an existing method on _BaseMemKraft (paranoia guard).
    if hasattr(_BaseMemKraft, _name):
        continue
    setattr(_BaseMemKraft, _name, _attr)

install_confidence_wrappers(_BaseMemKraft)

# v2.7.0 — wrap mutation methods so they auto-invalidate the search
# result cache. MUST run last so the wrappers see every other mixin's
# final method body.
install_cache_invalidation_wrappers(_BaseMemKraft)

# v3 Phase A: additive canonical names and warned legacy aliases. This runs
# after all wrappers so aliases preserve the already-shipped behavior.
from .compat import install_v3_compat
install_v3_compat(_BaseMemKraft)

MemKraft = _BaseMemKraft

__all__ = [
    "MemKraft",
    "ReasoningAuthorization",
    "ReasoningExecutionResult",
    "__version__",
]
