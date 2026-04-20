"""MemKraft — The compound knowledge system for AI agents"""

__version__ = "1.0.1"

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


MemKraft = _BaseMemKraft

__all__ = ["MemKraft", "__version__"]
