"""MemKraft 3.0 additive API compatibility and deprecation shims."""
from __future__ import annotations

import warnings
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable

from .resolver import resolver_dry_run


_INTERNAL_COMPAT_DISPATCH: ContextVar[bool] = ContextVar("memkraft_internal_compat_dispatch", default=False)


def _deprecated(alias: str, canonical: str, target: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(target)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        internal = _INTERNAL_COMPAT_DISPATCH.get()
        if not internal:
            warnings.warn(
                f"{alias}() is deprecated; use {canonical}() instead (removal no earlier than 4.0)",
                DeprecationWarning,
                stacklevel=2,
            )
        token = _INTERNAL_COMPAT_DISPATCH.set(True)
        try:
            return target(self, *args, **kwargs)
        finally:
            _INTERNAL_COMPAT_DISPATCH.reset(token)
    wrapper.__name__ = alias
    wrapper.__doc__ = f"Deprecated alias for :meth:`{canonical}`."
    return wrapper


def install_v3_compat(cls: type) -> None:
    """Install the additive 3.0 contract without removing legacy methods."""
    # Resolver was historically module-only. Expose its roadmap spelling on the
    # main object, while retaining a warned early-roadmap alias.
    def _resolver(self: Any, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return resolver_dry_run(claims)
    setattr(cls, "resolver_dry_run", _resolver)
    setattr(cls, "resolve_claims", _deprecated("resolve_claims", "resolver_dry_run", _resolver))

    setattr(cls, "record_event", _deprecated("record_event", "append_event", cls.append_event))

    legacy_search = cls.search
    mode_targets = {
        "legacy": legacy_search,
        "v2": cls.search_v2,
        "smart": cls.search_smart,
        "hybrid": cls.search_hybrid,
    }

    def search(self: Any, query: str, fuzzy: bool = False, top_k: Any = None,
               cache: bool = True, mode: str = "legacy", **kwargs: Any) -> Any:
        if mode not in mode_targets:
            raise ValueError("mode must be one of: legacy, v2, smart, hybrid")
        if mode == "legacy":
            if kwargs:
                raise TypeError(f"unexpected search arguments: {', '.join(sorted(kwargs))}")
            return legacy_search(self, query, fuzzy=fuzzy, top_k=top_k, cache=cache)
        target = mode_targets[mode]
        call_kwargs = dict(kwargs)
        if top_k is not None:
            call_kwargs["top_k"] = top_k
        token = _INTERNAL_COMPAT_DISPATCH.set(True)
        try:
            return target(self, query, **call_kwargs)
        finally:
            _INTERNAL_COMPAT_DISPATCH.reset(token)

    setattr(cls, "search", search)
    for name, target in (("search_v2", mode_targets["v2"]), ("search_smart", mode_targets["smart"]),
                         ("search_hybrid", mode_targets["hybrid"])):
        setattr(cls, name, _deprecated(name, f"search(mode='{name.removeprefix('search_')}')", target))
