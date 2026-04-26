"""MemKraft — The compound knowledge system for AI agents"""

__version__ = "2.3.3"

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
    MultimodalMixin,
    MultiPassMixin,
    RoutingMixin,
    RRFMixin,
    ConsolidationMixin,
    ContextCompressMixin,
    RerankMixin,
    ConfidenceMixin,
    TemporalChainMixin,
):
    for _name, _attr in vars(_mixin).items():
        if _name.startswith("__") and _name.endswith("__"):
            continue
        # don't silently overwrite something already defined on MemKraft
        if hasattr(_BaseMemKraft, _name) and not getattr(_mixin, _name) is _attr:
            # existing attr on MemKraft — only skip if it isn't our method
            if _name in ("__init__",):
                continue
        setattr(_BaseMemKraft, _name, _attr)


# v2.4 — wrap public search methods so every result list gains a
# ``confidence`` field.  Must run AFTER all mixins are attached so the
# wrappers see the final method bodies (search_multi from MultiPassMixin,
# search_v2 from SearchMixin, etc.).
install_confidence_wrappers(_BaseMemKraft)

MemKraft = _BaseMemKraft

__all__ = ["MemKraft", "__version__"]
