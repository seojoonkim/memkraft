"""
LongMemEval LLM backend adapter.

Lets harness.py & llm_judge.py call any of:
  - Anthropic native (default — identical to legacy behavior)
  - OpenAI Chat Completions
  - OpenRouter (OpenAI-compatible)
  - any OpenAI-compatible proxy (litellm-vhh, custom gateways, ...)

Selection via env (no CLI flag changes required):

  MK_LME_LLM_BACKEND   anthropic (default) | openai | openrouter | litellm-vhh
  MK_LME_LLM_MODEL     model id override (per backend)
  MK_LME_LLM_BASE_URL  base_url override (litellm-vhh / custom)
  MK_LME_LLM_API_KEY   explicit api key override

Public API (unchanged from existing call sites):

  make_client_with_messages_api() -> client
      client.messages.create(model=..., max_tokens=..., temperature=...,
                             messages=[{role, content}, ...]) -> resp
      resp.content[0].text         # str
      client.backend               # 'anthropic' | 'openai' | 'openrouter' | 'litellm-vhh'
      client.model                 # resolved default model
      client.RateLimitError        # exception class for outer retry loops

  get_backend()      -> Backend with .complete(prompt=..., max_tokens=...,
                                               temperature=..., model=...,
                                               max_retries=...) -> str
  describe_backend() -> human string for run.py header

Why an adapter (not just swapping anthropic.Anthropic.base_url):
  ANTHROPIC_API_KEY in this shell is an OAuth token (sk-ant-oat...) which
  the Anthropic SDK rejects with 401, and prior attempts to fan the
  Anthropic SDK out through OpenRouter failed at envelope decoding.
  Going through OpenAI Chat Completions is the lingua franca every proxy
  speaks, so we keep the Anthropic call shape on the harness side and
  translate inside this module.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional


# ------------------------------------------------------------------
# Backend table
# ------------------------------------------------------------------

# `claude_alias` = the closest model on each non-Anthropic backend; used to
# rewrite legacy `claude-haiku-4-5` model ids that callers still pass.
_BACKENDS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "default_model": "claude-haiku-4-5",
        "base_url": None,
        "api_key_env": "ANTHROPIC_API_KEY",
        "claude_alias": None,
    },
    "openai": {
        "default_model": "gpt-4o-mini",  # haiku-tier price/latency
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "claude_alias": "gpt-4o-mini",
    },
    "openrouter": {
        "default_model": "anthropic/claude-haiku-4-5",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "claude_alias": "anthropic/claude-haiku-4-5",
    },
    "litellm-vhh": {
        "default_model": "minpeter/sonnet-4.6",
        # Override-able via MK_LME_LLM_BASE_URL.
        "base_url": "https://litellm-vhh.minpeter.dev/v1",
        "api_key_env": "LITE_AGENT_API_KEY",
        "claude_alias": "minpeter/sonnet-4.6",
    },
}


def _backend_name() -> str:
    name = os.environ.get("MK_LME_LLM_BACKEND", "anthropic").strip().lower()
    if name not in _BACKENDS:
        raise ValueError(
            f"Unknown MK_LME_LLM_BACKEND={name!r}. "
            f"Choose one of: {', '.join(_BACKENDS)}"
        )
    return name


def resolve_model(model: Optional[str], backend_name: str) -> str:
    """Pick the actual model id sent over the wire.

    Priority:
      1. MK_LME_LLM_MODEL (env override always wins)
      2. caller-supplied `model`, with claude_alias rewrite for non-Anthropic
      3. backend default
    """
    env_model = os.environ.get("MK_LME_LLM_MODEL")
    if env_model:
        return env_model
    cfg = _BACKENDS[backend_name]
    if model:
        if backend_name != "anthropic" and model.lower().startswith("claude"):
            return cfg["claude_alias"] or cfg["default_model"]
        return model
    return cfg["default_model"]


# ------------------------------------------------------------------
# Anthropic-shaped response shim
# ------------------------------------------------------------------

@dataclass
class _Block:
    text: str


@dataclass
class _Response:
    content: list[_Block]


# ------------------------------------------------------------------
# Underlying client (does the real HTTP work)
# ------------------------------------------------------------------

class _LLMClient:
    """One per process. Holds either an anthropic.Anthropic or openai.OpenAI."""

    def __init__(self) -> None:
        self.backend = _backend_name()
        cfg = _BACKENDS[self.backend]
        self.cfg = cfg
        self.model = resolve_model(None, self.backend)

        if self.backend == "anthropic":
            import anthropic  # type: ignore
            api_key = (
                os.environ.get("MK_LME_LLM_API_KEY")
                or os.environ.get("ANTHROPIC_API_KEY")
            )
            kwargs: dict[str, Any] = {}
            if api_key:
                kwargs["api_key"] = api_key
            self._anthropic = anthropic.Anthropic(**kwargs)
            self.RateLimitError = anthropic.RateLimitError
            self._base_url = "anthropic-default"
        else:
            from openai import OpenAI, RateLimitError  # type: ignore
            api_key = (
                os.environ.get("MK_LME_LLM_API_KEY")
                or os.environ.get(cfg["api_key_env"])
            )
            if not api_key:
                raise RuntimeError(
                    f"backend={self.backend}: set MK_LME_LLM_API_KEY or "
                    f"{cfg['api_key_env']}"
                )
            base_url = os.environ.get("MK_LME_LLM_BASE_URL") or cfg["base_url"]
            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            self._openai = OpenAI(**kwargs)
            self.RateLimitError = RateLimitError
            self._base_url = base_url or "(none)"

    # --- chat call ---------------------------------------------------

    def chat(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float = 0.0,
        model: Optional[str] = None,
    ) -> _Response:
        mdl = resolve_model(model, self.backend)
        if self.backend == "anthropic":
            resp = self._anthropic.messages.create(
                model=mdl,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
            )
            text = resp.content[0].text if resp.content else ""
            return _Response(content=[_Block(text=text)])

        # OpenAI-compatible Chat Completions
        resp = self._openai.chat.completions.create(
            model=mdl,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )
        text = ""
        if resp.choices:
            ch = resp.choices[0]
            if ch.message and ch.message.content:
                text = ch.message.content
        return _Response(content=[_Block(text=text)])

    @property
    def description(self) -> str:
        return (
            f"backend={self.backend} model={self.model} "
            f"base_url={self._base_url}"
        )


# ------------------------------------------------------------------
# Public APIs
# ------------------------------------------------------------------

class _MessagesShim:
    """Mirrors anthropic.Anthropic().messages.create signature."""

    def __init__(self, client: _LLMClient) -> None:
        self._c = client

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        **_ignored: Any,
    ) -> _Response:
        return self._c.chat(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
        )


def make_client_with_messages_api() -> Any:
    """Return an object exposing `.messages.create(...)` like anthropic.Anthropic.

    Used by harness.py and llm_judge.py — they treat this as a drop-in for
    the legacy `anthropic.Anthropic()` client.
    """
    c = _LLMClient()
    obj = type("_LMEClient", (), {})()
    obj.messages = _MessagesShim(c)
    obj.backend = c.backend
    obj.model = c.model
    obj.RateLimitError = c.RateLimitError
    obj._llm = c  # for introspection / describe_backend
    return obj


# Convenience wrapper used by run.py's optional header line and any
# script that prefers a thin `complete(prompt=...)` API.

class _Backend:
    def __init__(self, llm: _LLMClient) -> None:
        self._llm = llm
        self.name = llm.backend
        self.model = llm.model
        self.description = llm.description

    def complete(
        self,
        *,
        prompt: str,
        max_tokens: int,
        temperature: float = 0.0,
        model: Optional[str] = None,
        max_retries: int = 3,
    ) -> str:
        last_err: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                resp = self._llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    model=model,
                )
                return resp.content[0].text if resp.content else ""
            except self._llm.RateLimitError:
                time.sleep(2 ** attempt)
            except Exception as e:
                last_err = e
                time.sleep(1)
        if last_err:
            raise last_err
        return ""


_cached: Optional[_LLMClient] = None


def _get_llm() -> _LLMClient:
    global _cached
    if _cached is None:
        _cached = _LLMClient()
    return _cached


def get_backend() -> _Backend:
    return _Backend(_get_llm())


def describe_backend() -> str:
    try:
        return _get_llm().description
    except Exception as e:
        return f"<unresolved: {e}>"


def reset_for_testing() -> None:
    global _cached
    _cached = None
