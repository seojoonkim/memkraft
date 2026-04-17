"""MemKraft — The compound knowledge system for AI agents"""

__version__ = "0.8.1"

from .core import MemKraft as _BaseMemKraft
from .bitemporal import BitemporalMixin
from .decay import DecayMixin
from .links import LinksMixin
from .tiers import TiersMixin


# v0.8.0: extend MemKraft in-place with new mixins so every existing
# ``from memkraft import MemKraft`` continues to work unchanged.  The
# mixin methods are added to the class object itself — we don't subclass
# because existing call sites (and tests) do ``MemKraft(...)``.
for _mixin in (BitemporalMixin, DecayMixin, LinksMixin, TiersMixin):
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
