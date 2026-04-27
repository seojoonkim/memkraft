#!/usr/bin/env python3
"""PersonaMem Benchmark Harness — Validator Variant

Combines two strategies to beat raw baseline even with strong LLMs:

  1. Smart Retrieval — MemKraft picks 3-5 most-relevant conversation
     sessions for the question (keyword + topic + temporal signals),
     then builds a compressed context (persona + selected sessions).
     This shrinks the 26K-token conversation to ~5-8K tokens while
     keeping every raw sentence the model would need.

  2. Answer Validation — after the model emits an answer, MemKraft
     checks it against stored preferences / dislikes. If a known
     "dislike X" contradicts a recommended "X", we re-query the model
     with the contradicting fact in the prompt so it can self-correct.

Runs two variants: baseline (raw conversation) + validator.

CLI:
    python3 harness_validator.py --split 32k --model gpt-4.1 --max-questions 0
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from memkraft import MemKraft  # noqa: E402
from memkraft.personamem import (  # noqa: E402
    PersonaMemAdapter,
    infer_category,
    strip_role_prefix,
)

# Reuse data loading, LLM query, extraction, and the baseline context
# from harness_v3 to avoid code duplication.
from harness_v3 import (  # type: ignore  # noqa: E402
    load_persona_mem,
    query_llm,
    extract_answer,
    build_baseline_context,
    QTYPE_MAP,
    _empty_results,
    _accumulate,
    _finalize,
    _load_checkpoint,
    _save_checkpoint,
)


# ────────────────────────────────────────────────────────────
# Codex CLI Subprocess Adapter (Day 2.5)
#   ChatGPT OAuth via `codex` CLI → no API credit needed.
#   Mimics openai.OpenAI() / openai.AsyncOpenAI() interface.
#   Activated via env: MEMCRAFT_LLM_PROVIDER=codex (or MEMCRAFT_USE_CODEX=1)
# ────────────────────────────────────────────────────────────

import re as _re_codex  # local alias to avoid shadowing


class _CodexChoice:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()
        self.finish_reason = "stop"
        self.index = 0


class _CodexResponse:
    def __init__(self, content: str, model: str):
        self.choices = [_CodexChoice(content)]
        self.model = model
        self.usage = type("U", (), {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        })()


def _codex_format_messages(messages: list) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _codex_extract_answer(output: str) -> str:
    """Extract assistant content from `codex exec` stdout.

    Format observed (codex 0.125.0):
        <header banner>
        --------
        user
        <prompt>

        codex
        <answer text — possibly multi-line>
        [optional ERROR log lines from rollout writer]
        tokens used
        <number>
        <maybe answer echo>
    """
    # Primary: greedy capture between 'codex\n' and 'tokens used'
    m = _re_codex.search(r"\ncodex\n(.+?)\ntokens used", output, _re_codex.DOTALL)
    if m:
        body = m.group(1)
        # Strip stray ERROR log lines that codex may emit between answer
        # and 'tokens used' (e.g. 'failed to record rollout items').
        cleaned = []
        for line in body.splitlines():
            if _re_codex.search(r"ERROR codex_core::", line):
                continue
            if _re_codex.search(r"^\d{4}-\d{2}-\d{2}T.*Z\s+(ERROR|WARN|INFO)", line):
                continue
            cleaned.append(line)
        return "\n".join(cleaned).strip()
    # Fallback: last non-empty line before EOF
    lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
    return lines[-1] if lines else output.strip()


class _CodexSyncCompletions:
    def create(self, model, messages,
               max_completion_tokens=None, max_tokens=None, **kwargs):
        import subprocess
        prompt = _codex_format_messages(messages)
        try:
            result = subprocess.run(
                ["codex", "exec", "--model", model, "--skip-git-repo-check"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired as e:
            raise Exception(f"codex CLI timeout (180s) for model {model}") from e
        answer = _codex_extract_answer(result.stdout or "")
        return _CodexResponse(answer, model)


class _CodexAsyncCompletions:
    async def create(self, model, messages,
                     max_completion_tokens=None, max_tokens=None, **kwargs):
        import asyncio
        prompt = _codex_format_messages(messages)
        proc = await asyncio.create_subprocess_exec(
            "codex", "exec", "--model", model, "--skip-git-repo-check",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode()), timeout=240
            )
        except asyncio.TimeoutError as e:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise Exception("codex CLI async timeout (240s)") from e
        output = stdout.decode(errors="replace")
        answer = _codex_extract_answer(output)
        return _CodexResponse(answer, model)


class CodexSubprocessAdapter:
    """OpenAI-compatible adapter that shells out to `codex exec`.

    Sync usage:
        client = CodexSubprocessAdapter()
        client.chat.completions.create(model="gpt-5.5", messages=[...])
    """

    def __init__(self):
        self._sync = _CodexSyncCompletions()

    @property
    def chat(self):
        return type("C", (), {"completions": self._sync})()


class CodexSubprocessAsyncAdapter:
    """Async OpenAI-compatible adapter that shells out to `codex exec`."""

    def __init__(self):
        self._async = _CodexAsyncCompletions()

    @property
    def chat(self):
        return type("C", (), {"completions": self._async})()


def _use_codex() -> bool:
    provider = os.environ.get("MEMCRAFT_LLM_PROVIDER", "").lower()
    return provider == "codex" or bool(os.environ.get("MEMCRAFT_USE_CODEX"))


# ────────────────────────────────────────────────────────────
# Stopwords (same list used elsewhere for keyword extraction)
# ────────────────────────────────────────────────────────────

_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "at", "for", "with", "about", "from", "by",
    "as", "if", "or", "and", "but", "not", "no", "yes", "do", "did",
    "does", "have", "had", "has", "my", "your", "our", "their", "his",
    "her", "this", "that", "these", "those", "there", "here",
    "i", "you", "we", "they", "it", "he", "she", "me", "him", "us",
    "them", "recommend", "recommendation", "suggestion", "suggest",
    "tell", "please", "would", "could", "should", "will", "can", "may",
    "what", "when", "where", "how", "why", "who", "which",
    "something", "anything", "everything", "new", "old", "any",
}


def _keywords(text: str) -> List[str]:
    """Extract content keywords from a question/topic string."""
    if not text:
        return []
    toks = re.findall(r"[a-zA-Z][a-zA-Z\-']{2,}", text.lower())
    return [t for t in toks if t not in _STOP and len(t) >= 3]


# ────────────────────────────────────────────────────────────
# Session helpers
# ────────────────────────────────────────────────────────────

def _split_into_sessions(
    context: List[Dict[str, Any]], end_idx: int
) -> List[Dict[str, Any]]:
    """Split a PersonaMem context into ordered session blocks.

    Uses the same greeting-based heuristic as PersonaMemAdapter, but
    returns the actual message slices so we can score them.

    Returns list of dicts:
        {
          "session_num": int (1-indexed),
          "start_idx": int,
          "end_idx": int,   # exclusive
          "messages": List[Dict],  # raw conversation messages (no system)
          "text": str,      # joined lowercase user+assistant text
        }
    """
    greetings = (
        "hi there", "hello", "hey there", "good morning", "good evening",
        "how's it going", "how are you", "what's up",
    )

    # Index bounds — never cross end_idx
    upper = min(end_idx, len(context))
    if upper <= 0:
        return []

    sessions: List[Dict[str, Any]] = []
    current_msgs: List[Dict[str, Any]] = []
    current_start = 0
    prev_had_assistant = False

    first_nonsystem = None
    for i in range(upper):
        if context[i].get("role") != "system":
            first_nonsystem = i
            break
    if first_nonsystem is None:
        return []
    current_start = first_nonsystem

    for i in range(first_nonsystem, upper):
        msg = context[i]
        role = msg.get("role")
        if role == "system":
            continue
        content_stripped = strip_role_prefix(msg.get("content") or "").lower()
        is_greeting = any(content_stripped.startswith(g) for g in greetings)

        if role == "user" and is_greeting and prev_had_assistant and current_msgs:
            sessions.append({
                "session_num": len(sessions) + 1,
                "start_idx": current_start,
                "end_idx": i,
                "messages": current_msgs,
            })
            current_msgs = []
            current_start = i
            prev_had_assistant = False

        current_msgs.append(msg)
        if role == "assistant":
            prev_had_assistant = True

    if current_msgs:
        sessions.append({
            "session_num": len(sessions) + 1,
            "start_idx": current_start,
            "end_idx": upper,
            "messages": current_msgs,
        })

    # Fallback: if only 1 greeting-delimited session was detected but
    # it's long, split it into fixed-size blocks so the selector can
    # pick the best region.
    if len(sessions) == 1 and len(sessions[0]["messages"]) > 40:
        big = sessions[0]["messages"]
        chunk = 30
        rebuilt: List[Dict[str, Any]] = []
        for i_start in range(0, len(big), chunk):
            block = big[i_start:i_start + chunk]
            if not block:
                continue
            rebuilt.append({
                "session_num": len(rebuilt) + 1,
                "start_idx": sessions[0]["start_idx"] + i_start,
                "end_idx": sessions[0]["start_idx"] + i_start + len(block),
                "messages": block,
            })
        sessions = rebuilt

    # Build searchable text per session
    for s in sessions:
        chunks: List[str] = []
        for m in s["messages"]:
            c = strip_role_prefix(m.get("content") or "")
            if c:
                chunks.append(c.lower())
        s["text"] = "\n".join(chunks)
        s["category"] = infer_category(s["text"])

    return sessions



# ────────────────────────────────────────────────────────────
# Smart session selection (Pass 1 — retrieval)
# ────────────────────────────────────────────────────────────

def select_relevant_sessions(
    mk: MemKraft,
    persona_name: str,
    question: str,
    topic: str,
    sessions: List[Dict[str, Any]],
    max_sessions: int = 5,
) -> List[Dict[str, Any]]:
    """Pick the 3-5 sessions most relevant to (question, topic).

    v2-upgrade (2026-04-25): instead of raw keyword intersection we
    register every session as a tracked document and call MemKraft's
    BM25-IDF (`search_smart`) so we get the same retrieval quality as
    the AMB benchmark. Falls back to the old keyword/category/recency
    scoring if the smart-search returns nothing useful.

    v3-upgrade (2026-04-27): in addition to ``search_smart`` we now
    also fire ``search_precise`` (exact-token / IDF-weighted match)
    and fuse the two ranked lists via simple weighted average. Reason:
    PersonaMem preference questions tend to cite specific entities
    ("Miami hotel", "commute activities") that the smart sentence
    embedder dilutes; ``search_precise`` keeps those signals sharp.

    Always include the most recent session (the "now" anchor) even
    if its smart-search score is weak, because many PersonaMem
    questions are about the user's latest state.
    """
    if not sessions:
        return []

    # If we have few sessions total, just include them all — there's
    # nothing to compress, and dropping any risks losing ground truth.
    if len(sessions) <= max_sessions:
        return sorted(sessions, key=lambda s: s["session_num"])

    total = len(sessions)

    # ── v2 path: register each session as a doc and run search_smart ──
    smart_scores: Dict[int, float] = {}
    try:
        # Use a unique tag per (persona, question hash) so repeated
        # calls inside one base_dir don't collide. Question text is
        # short and unique enough — md5 keeps doc_ids deterministic.
        import hashlib
        tag = hashlib.md5(
            f"{persona_name}|{question}|{topic}".encode("utf-8")
        ).hexdigest()[:10]

        for s in sessions:
            doc_id = f"hv_sess_{tag}_{s['session_num']}"
            content = s.get("text") or ""
            if content.strip():
                try:
                    mk.track_document(
                        doc_id=doc_id,
                        content=content,
                        chunk_size=500,
                        chunk_overlap=50,
                        entity_type="chunk",
                        source="persona_mem_session",
                    )
                except Exception:
                    # best-effort: ignore individual tracking failures
                    pass

        query = f"{topic} {question}".strip()
        prefix = f"hv_sess_{tag}_"

        def _ingest_session_hits(hits, target: Dict[int, float]) -> None:
            """Update *target* with best per-session score from *hits*."""
            for h in hits or []:
                ent = str(h.get("entity") or h.get("id") or "")
                if not ent.startswith(prefix):
                    continue
                tail = ent[len(prefix):]
                # Strip chunk suffix '__cN' if present
                if "__c" in tail:
                    tail = tail.split("__c", 1)[0]
                try:
                    num = int(tail)
                except ValueError:
                    continue
                score = float(h.get("score", 0.0) or 0.0)
                if score > target.get(num, 0.0):
                    target[num] = score

        # ── 1. search_smart (BM25-IDF, broader recall) ──
        try:
            smart_hits = mk.search_smart(query, top_k=max_sessions * 6) or []
        except Exception:
            smart_hits = []
        _ingest_session_hits(smart_hits, smart_scores)

        # ── 2. search_precise (exact-token / IDF-weighted, sharper) ──
        # v3-upgrade (2026-04-27): keep entity tokens like "Miami"
        # or "commute" from being averaged out by sentence embedding.
        precise_scores: Dict[int, float] = {}
        try:
            precise_hits = mk.search_precise(query, top_k=max_sessions * 6) or []
            _ingest_session_hits(precise_hits, precise_scores)
        except Exception:
            precise_scores = {}

        # ── 3. Fuse: normalise each list, then weighted-sum ──
        if precise_scores:
            def _normalise(d: Dict[int, float]) -> Dict[int, float]:
                if not d:
                    return {}
                m = max(d.values()) or 1.0
                return {k: v / m for k, v in d.items()}

            n_smart = _normalise(smart_scores)
            n_prec = _normalise(precise_scores)
            fused: Dict[int, float] = {}
            for k in set(n_smart) | set(n_prec):
                fused[k] = 0.6 * n_smart.get(k, 0.0) + 0.4 * n_prec.get(k, 0.0)
            smart_scores = fused
    except Exception:
        smart_scores = {}

    # ── Fallback signals (kept as a safety net, weighted lighter) ──
    kws = set(_keywords(question) + _keywords(topic))
    q_category = infer_category(f"{topic} {question}")

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for s in sessions:
        text = s["text"]
        smart = smart_scores.get(s["session_num"], 0.0)
        if kws:
            hits = sum(1 for k in kws if k in text)
            kw_score = hits / max(1, len(kws))
        else:
            kw_score = 0.0
        cat_score = 1.0 if s.get("category") == q_category else 0.0
        recency = s["session_num"] / total if total else 0.0

        # smart_score dominates when present; keyword/category/recency
        # remain as tie-breakers so behaviour degrades gracefully.
        score = (4.0 * smart) + (1.0 * kw_score) + (0.4 * cat_score) + (0.4 * recency)
        scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Always include the last session — it anchors "current" preferences
    last_session = sessions[-1]
    picked: List[Dict[str, Any]] = []
    picked_ids = set()

    for score, s in scored:
        if len(picked) >= max_sessions:
            break
        if score <= 0 and last_session["session_num"] not in picked_ids:
            continue
        picked.append(s)
        picked_ids.add(s["session_num"])

    if last_session["session_num"] not in picked_ids:
        # Replace the weakest pick with the last session
        if len(picked) >= max_sessions:
            picked[-1] = last_session
        else:
            picked.append(last_session)
        picked_ids.add(last_session["session_num"])

    # If we still have headroom and nothing was picked (all zero scores),
    # fall back to the last max_sessions sessions (most recent is likely
    # most relevant for PersonaMem).
    if not picked:
        picked = sessions[-max_sessions:]

    # Restore chronological order for the prompt
    picked.sort(key=lambda s: s["session_num"])
    return picked


# ────────────────────────────────────────────────────────────
# P1 (2026-04-26): Persona summary card injection
# ────────────────────────────────────────────────────────────
# Rationale: Validator graph reasoning (B2/B2.5) inspects answers
# *after* the LLM has already chosen. The LLM never sees the user's
# stored prefs in Pass 1, so it has to rederive them from retrieved
# sessions every turn. P1 fixes that by prepending a short, curated
# preference card to the system prompt before the LLM answers.
# Opt-in via env: MEMCRAFT_USE_PERSONA_CARD=1 (default OFF so we can
# A/B safely against earlier baselines).

_NEGATIVE_PREF_KEYS = (
    "dislike", "avoid", "not_", "discontinued",
    "hate", "anti", "stopped", "quit",
)


def _is_clean_pref_value(value: Any) -> bool:
    """Reject narrative-dump preference values; keep short, specific ones."""
    if not value or not isinstance(value, str):
        return False
    v = value.strip()
    if not v or len(v) > 80:
        return False
    tokens = v.split()
    if len(tokens) > 15:
        return False
    if v.count(".") >= 2 or v.count(",") >= 2:
        return False
    return True


def _is_negative_pref_key(key: Any) -> bool:
    if not key or not isinstance(key, str):
        return False
    k = key.lower()
    return any(neg in k for neg in _NEGATIVE_PREF_KEYS)


def _format_persona_card(
    mk: MemKraft, persona_name: str, top_n: int = 8,
) -> str:
    """Compose a compact 'what we know about the user' system block.

    Selection rules:
      * Filter out narrative dumps (long values, multi-sentence).
      * Take up to 5 negative-polarity prefs (dislikes / avoids /
        discontinued / etc.) — these are the highest-leverage cues
        for PersonaMem (preference_reasons, aligned_recommendation).
      * Top up with up to 3 positive-polarity prefs.
      * If nothing survives the clean filter, return "" so the prompt
        is unchanged (zero-regression by construction).
    """
    try:
        all_prefs = mk.pref_get(persona_name) or []
    except Exception:
        return ""
    if not all_prefs:
        return ""

    clean = [p for p in all_prefs if _is_clean_pref_value(p.get("value"))]
    if not clean:
        return ""

    negs: List[Dict[str, Any]] = []
    poss: List[Dict[str, Any]] = []
    for p in clean:
        if _is_negative_pref_key(p.get("key")):
            negs.append(p)
        else:
            poss.append(p)

    # Prefer high-strength entries (pref_get already filters by
    # validity window, so we just sort by strength desc).
    def _strength(p: Dict[str, Any]) -> float:
        try:
            return float(p.get("strength") or 0.5)
        except Exception:
            return 0.5

    negs.sort(key=_strength, reverse=True)
    poss.sort(key=_strength, reverse=True)

    n_neg = min(5, len(negs), top_n)
    n_pos = min(3, len(poss), top_n - n_neg)
    selected = negs[:n_neg] + poss[:n_pos]
    if not selected:
        return ""

    lines = ["## What we know about the user", ""]
    for p in selected:
        key = p.get("key") or "?"
        val = p.get("value") or "?"
        marker = "❌" if _is_negative_pref_key(key) else "✓"
        lines.append(f"- {marker} {key}: {val}")
    lines.append("")
    lines.append(
        "**Important**: If a fact above contradicts an answer choice, "
        "that choice is almost certainly wrong."
    )
    return "\n".join(lines)


# v3 (2026-04-27): question types where we want to nudge the LLM toward
# the user's previously stated *direct* preferences instead of generic
# best-practice answers. Sourced from QTYPE_MAP (harness_v3.py).
_PREFERENCE_QTYPES = frozenset({
    "acknowledge_latest_preferences",
    "track_full_preference_evolution",
    "revisit_reasons_behind_preference_updates",
    "recalling_the_reasons_behind_previous_updates",
    "provide_preference_aligned_recommendations",
})

_PREFERENCE_GUIDANCE = (
    "IMPORTANT — preference question detected.\n"
    "This question asks what THIS USER (not a generic person) would prefer, "
    "want, or do. Follow these rules strictly:\n"
    "  1. Anchor on the user's most recent *explicit* statements in the "
    "sessions above (e.g. \"I want to stay connected via virtual "
    "team-building and regular check-ins\", \"I prefer the Miami beachfront "
    "hotel\", \"during my commute I listen to language podcasts\").\n"
    "  2. If two options are both reasonable, pick the one that names a "
    "specific habit / item / place / activity the user has already "
    "endorsed — NOT the one that describes a generic structured policy "
    "(e.g. prefer \"virtual team-building and check-ins\" over \"structured "
    "optional activities\"; prefer \"language podcasts\" over \"productive "
    "audio content\"; prefer the named hotel over \"a well-rated hotel\").\n"
    "  3. Treat phrases the user explicitly disliked or stopped doing as "
    "hard exclusions, even if the option sounds plausible in general.\n"
    "  4. When the question asks \"what would the user prefer / want / do?\" "
    "infer the user's *direct* preference from their own words; do not "
    "substitute a more abstract or politically-neutral paraphrase."
)


def build_compressed_context(
    mk: MemKraft,
    persona_name: str,
    question: str,
    topic: str,
    context: List[Dict[str, Any]],
    end_idx: int,
    max_sessions: int = 5,
    qtype: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]]]:
    """Build the Pass-1 compressed prompt: persona + selected sessions.

    Returns (messages_for_llm, selected_sessions).
    The messages list starts with the persona system message (original),
    followed by the selected conversation messages in chronological order.

    If *qtype* indicates a preference-style question (see
    ``_PREFERENCE_QTYPES``), an extra system message is appended that
    nudges the model away from generic / structured-policy answers and
    toward the user's own past phrasing. (v3 — 2026-04-27)
    """
    # Original system / persona message (unchanged — the persona bio
    # is part of the ground truth the model needs)
    system_msg: Optional[Dict[str, Any]] = None
    for msg in context[:end_idx]:
        if msg.get("role") == "system":
            system_msg = msg
            break

    sessions = _split_into_sessions(context, end_idx)
    picked = select_relevant_sessions(
        mk, persona_name, question, topic, sessions, max_sessions=max_sessions,
    )

    # Session-separator header so the model can see discrete chunks
    msgs: List[Dict[str, str]] = []

    # P1: optional persona summary card. Goes BEFORE the persona bio
    # so the LLM sees it as a primary cue rather than buried after the
    # bio + relevance note. Off by default; flip on via env.
    if os.environ.get("MEMCRAFT_USE_PERSONA_CARD"):
        card = _format_persona_card(mk, persona_name)
        if card:
            msgs.append({"role": "system", "content": card})

    if system_msg:
        msgs.append({"role": "system", "content": system_msg.get("content", "")})

    # Add a lightweight system note explaining the compression
    msgs.append({
        "role": "system",
        "content": (
            f"The following are the {len(picked)} most relevant past "
            "conversation sessions with this user (out of "
            f"{len(sessions)} total). They are shown in chronological "
            "order. Use them — together with the persona profile above "
            "— to answer the next question accurately."
        ),
    })

    for s in picked:
        msgs.append({
            "role": "system",
            "content": f"--- Session {s['session_num']} ---",
        })
        for m in s["messages"]:
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            msgs.append({"role": role, "content": m.get("content", "")})

    # v3 (2026-04-27): append preference-guidance AFTER the sessions so
    # the LLM reads it as the most recent system instruction right
    # before the question turn. Only fires for preference-type qtypes.
    if qtype and qtype in _PREFERENCE_QTYPES:
        msgs.append({"role": "system", "content": _PREFERENCE_GUIDANCE})

    return msgs, picked



# ────────────────────────────────────────────────────────────
# Answer validation (Pass 2 — fact-check)
# ────────────────────────────────────────────────────────────

_ANSWER_LETTER_RE = re.compile(r"\(?([a-d])\)?", re.IGNORECASE)


def _parse_options(all_options: str) -> Dict[str, str]:
    """Parse the multi-choice options block into {letter: text}."""
    out: Dict[str, str] = {}
    # Match "(a) some text" up to the next "(b)" / end
    for m in re.finditer(
        r"\(([a-d])\)\s*(.+?)(?=\([a-d]\)|$)", all_options, re.IGNORECASE | re.DOTALL
    ):
        letter = m.group(1).lower()
        text = m.group(2).strip().rstrip(",").strip()
        out[letter] = text
    return out


def _predicted_letter(predicted: str) -> Optional[str]:
    """Extract the predicted letter from the LLM reply."""
    if not predicted:
        return None
    pred = predicted.strip()
    if "<final_answer>" in pred:
        pred = pred.split("<final_answer>")[-1].strip()
    if pred.endswith("</final_answer>"):
        pred = pred[: -len("</final_answer>")].strip()
    pred = re.sub(r"<[^>]+>", "", pred).strip()
    opts = re.findall(r"\(([a-d])\)", pred.lower())
    if opts:
        return opts[-1]
    letters = re.findall(r"\b([a-d])\b", pred.lower())
    if letters:
        return letters[-1]
    return None


def fact_check_answer(
    mk: MemKraft,
    persona_name: str,
    predicted: str,
    question: str,
    all_options: str,
) -> Optional[str]:
    """Check the predicted answer against MemKraft's stored preferences.

    Returns a contradiction note (plain English) if the answer conflicts
    with a known dislike / discontinued preference, else ``None``.

    We only flag the *most informative* single contradiction to avoid
    nudging the model toward a random alternative.
    """
    letter = _predicted_letter(predicted)
    if not letter:
        return None

    options = _parse_options(all_options)
    answer_text = options.get(letter, "")
    if not answer_text:
        return None

    answer_low = answer_text.lower()

    # Load preferences
    try:
        prefs = mk.pref_get(persona_name) or []
    except Exception:
        prefs = []
    if not prefs:
        return None

    # Strict fact-check: we ONLY flag when a SHORT, SPECIFIC dislike
    # value (persona-level or explicitly worded) appears as a
    # substring in the answer. This rules out:
    #   - long rambling sentiment-derived dislikes
    #   - token-overlap false positives
    # Rationale: MemKraft's adapter generates many noisy "dislike"
    # entries from neutral-sentiment narrative; a wide net hurts us.

    # v2-upgrade (2026-04-25): expanded NEGATIVE_KEYS + relaxed length
    # cap + token-jaccard fallback so the validator can fire on more
    # than 0.09% of cases while still rejecting noisy narrative dislikes.
    NEGATIVE_KEYS = {
        "dislikes", "discontinued", "persona_dislike",
        "not_like", "avoid", "avoids", "stopped", "quit",
        "quit_doing", "hates", "disliked",
    }

    def _token_set(text: str) -> set:
        toks = re.findall(r"[a-zA-Z][a-zA-Z\-']+", (text or "").lower())
        return {t for t in toks if t not in _STOP and len(t) >= 3}

    def _token_jaccard(a: str, b: str) -> float:
        ta = _token_set(a)
        tb = _token_set(b)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    answer_tokens = _token_set(answer_text)

    best_match: Optional[Dict[str, Any]] = None
    best_match_score: float = 0.0  # higher is stronger evidence

    for p in prefs:
        key = (p.get("key") or "").lower()
        value = (p.get("value") or "").strip()
        if not value or len(value) < 3:
            continue

        pref_is_negative = (
            key in NEGATIVE_KEYS
            or key.startswith("persona_dislike")
            or key.startswith("not_")
            or key.startswith("avoid")
            or key.startswith("stop")
        )
        if not pref_is_negative:
            continue

        # Relaxed quality filter: allow up to 200 chars / 25 tokens.
        # Truly narrative noise will fail the jaccard test below.
        if len(value) > 200:
            continue
        token_count = len(re.findall(r"[a-zA-Z][a-zA-Z\-']+", value))
        if token_count > 25:
            continue

        value_low = value.lower().strip(" .,:;!?")
        value_low = re.sub(r"^(?:the|a|an)\s+", "", value_low)
        if len(value_low) < 3:
            continue

        # Tier 1: exact word-boundary substring (strongest signal)
        pattern = r"\b" + re.escape(value_low) + r"\b"
        if re.search(pattern, answer_low):
            score = 2.0 + min(len(value_low), 80) / 80.0
            if score > best_match_score:
                best_match = p
                best_match_score = score
            continue

        # Tier 2: token-jaccard >= 0.4 (catches paraphrased dislikes)
        if not answer_tokens:
            continue
        jacc = _token_jaccard(value, answer_text)
        if jacc >= 0.4:
            score = 1.0 + jacc  # always < tier-1 ceiling, > tier-1 floor
            if score > best_match_score:
                best_match = p
                best_match_score = score

    if best_match:
        return (
            f"The user has previously indicated they do NOT like "
            f"\"{best_match.get('value')}\" (stored preference: "
            f"{best_match.get('key')}). Your answer \"{answer_text}\" "
            "appears to include or recommend this."
        )

    # ── B2 (2026-04-25) — graph-reasoning supplement ───────────────
    # When jaccard/substring match misses but the preference graph
    # encodes a negative relation toward an entity that overlaps with
    # the answer, surface that as a contradiction. Opt-in via
    # MEMCRAFT_USE_GRAPH_REASONING=1 so we can A/B against the
    # text-only validator. Failures are silent — the graph signal is
    # purely additive.
    if os.environ.get("MEMCRAFT_USE_GRAPH_REASONING"):
        # B2.5 (2026-04-25) — conservative graph signal.
        # B2 returned a contradiction on a single-token match, which
        # blew up false-positives (32k 30q: 80% → 46.7%). Now:
        #   1. strip stop-words from target tokens
        #   2. require >= 2 matched tokens (or 1 long, length>=6)
        #   3. score = matches*0.3 + length-bonus, only fire if >= 1.5
        #   4. only fire when jaccard scoring above missed
        #      (this branch is already gated on best_match=None)
        _GRAPH_STOP_WORDS = frozenset([
            'like', 'time', 'my', 'the', 'a', 'an', 'and', 'or', 'of',
            'to', 'for', 'with', 'in', 'on', 'at', 'by', 'from',
            'that', 'this', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did',
            'will', 'would', 'could', 'should', 'i', 'you', 'he',
            'she', 'we', 'they', 'it', 'me', 'him', 'her', 'us',
            'them', 'his', 'hers', 'their', 'our', 'your', 'its',
            'really', 'very', 'more', 'most', 'much', 'just', 'so',
            'too', 'not', 'no', 'yes', 'as', 'if', 'but', 'about',
            'into', 'than', 'then', 'when', 'where', 'why', 'how',
            'what', 'who', 'which', 'some', 'any', 'all', 'each',
            'every', 'few', 'lot', 'lots', 'one', 'two', 'thing',
            'things', 'something', 'anything', 'someone', 'anyone',
            'people', 'person', 'user', 'users',
        ])

        def _graph_score(target_text: str, picked_option_low: str) -> tuple:
            """(score, matched_tokens) for a graph target vs answer."""
            t_tokens = [
                t for t in re.split(r"[^a-z0-9]+", target_text.lower())
                if t and t not in _GRAPH_STOP_WORDS and len(t) >= 3
            ]
            if not t_tokens:
                return 0.0, []
            matches = []
            for tok in t_tokens:
                if re.search(r"\b" + re.escape(tok) + r"\b", picked_option_low):
                    matches.append(tok)
            if not matches:
                return 0.0, []
            longest = max(len(m) for m in matches)
            score = len(matches) * 0.3 + (0.2 if longest >= 6 else 0.0)
            return score, matches

        try:
            graph_results = mk.reason_preference_via_graph(
                entity=persona_name,
                query=f"{question} {answer_text}",
                max_hops=2,
            )
            _NEG_REL_TOKENS = (
                "dislike", "avoid", "not_", "discontinued",
                "hate", "anti", "stopped", "quit",
            )
            graph_max_score = 0.0
            graph_best_relation = None
            graph_best_target = None
            graph_best_matches: List[str] = []
            graph_best_reasons: List[str] = []
            for r in graph_results or []:
                relation = (r.get("relation") or "").lower()
                if not any(neg in relation for neg in _NEG_REL_TOKENS):
                    continue
                target = (r.get("target") or "").lower()
                if not target:
                    continue
                score, matches = _graph_score(target, answer_low)
                # require at least 2 matched tokens, OR 1 long token (>=6 chars)
                if len(matches) < 2 and not any(len(m) >= 6 for m in matches):
                    continue
                if score > graph_max_score:
                    graph_max_score = score
                    graph_best_relation = relation
                    graph_best_target = target
                    graph_best_matches = matches
                    graph_best_reasons = r.get("reasons") or []

            # threshold gate — graph-only contradictions need score >= 1.5
            if graph_max_score >= 1.5 and graph_best_target:
                reason_clause = (
                    f" (reason: {', '.join(graph_best_reasons[:2])})"
                    if graph_best_reasons else ""
                )
                return (
                    f"Graph reasoning indicates the user has a "
                    f"'{graph_best_relation}' relation toward "
                    f"'{graph_best_target.replace('-', ' ')}' "
                    f"(matched: {', '.join(graph_best_matches[:3])}, "
                    f"score={graph_max_score:.2f}){reason_clause}. "
                    f"Your answer \"{answer_text}\" appears to include "
                    "or recommend this."
                )
        except Exception:
            pass  # graceful — graph layer is best-effort

    return None


def query_with_validation(
    mk: MemKraft,
    persona_name: str,
    question: str,
    all_options: str,
    context: List[Dict[str, Any]],
    end_idx: int,
    topic: str,
    model: str,
    max_retries: int = 1,
    max_sessions: int = 5,
    qtype: Optional[str] = None,
) -> Dict[str, Any]:
    """Full 2-pass pipeline: Smart retrieval → answer → fact-check → retry.

    Returns a dict:
      {
        "answer": <final model reply>,
        "first_answer": <pass-1 reply>,
        "revised": bool,
        "contradiction": <str or None>,
        "n_sessions_selected": int,
      }
    """
    # ── Pass 1 — compressed retrieval + generation ──────────
    msgs, picked = build_compressed_context(
        mk, persona_name, question, topic, context, end_idx,
        max_sessions=max_sessions, qtype=qtype,
    )
    first_answer = query_llm(question, all_options, msgs, model=model)

    # ── Pass 2 — validation ──────────────────────────────────
    contradiction = fact_check_answer(
        mk, persona_name, first_answer, question, all_options,
    )

    if not contradiction or max_retries <= 0:
        return {
            "answer": first_answer,
            "first_answer": first_answer,
            "revised": False,
            "contradiction": contradiction,
            "n_sessions_selected": len(picked),
        }

    # ── Re-query with the contradiction surfaced ─────────────
    # Build the original question turn so the model re-reads it, then
    # its first answer, then our correction. Call OpenAI directly so
    # we don't duplicate the question (query_llm would re-append it).
    import openai

    def _make_openai_client():
        """Return openai client, routing via OpenRouter when env hint set.

        If MEMCRAFT_LLM_PROVIDER=openrouter (or model id contains '/'),
        use OpenRouter base_url + OPENROUTER_API_KEY. Otherwise default
        to plain OpenAI.
        """
        provider = os.environ.get("MEMCRAFT_LLM_PROVIDER", "").lower()
        # codex CLI branch (Day 2.5): ChatGPT OAuth via subprocess
        if _use_codex():
            return CodexSubprocessAdapter()
        # litellm-vhh branch (Day 3): explicit provider OR LITELLM_VHH_KEY set
        if provider == "litellm-vhh" or os.environ.get("LITELLM_VHH_KEY"):
            return openai.OpenAI(
                base_url="https://llm.vhh.sh/v1",
                api_key=os.environ.get(
                    "LITELLM_VHH_KEY",
                    "sk-litellm-local-58e6dff127b675454d6cc518918738974c67fb9395b47ebd",
                ),
                default_headers={"User-Agent": "OpenAI/Python 1.50.0"},
            )
        use_or = provider == "openrouter" or (
            "/" in str(model) and os.environ.get("OPENROUTER_API_KEY")
        )
        if use_or:
            return openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=os.environ["OPENROUTER_API_KEY"],
            )
        return openai.OpenAI()
    instructions = (
        "Find the most appropriate model response and give your final "
        "answer (a), (b), (c), or (d) after the special token "
        "<final_answer>."
    )
    revision_msgs = list(msgs) + [
        {
            "role": "user",
            "content": f"{question}\n\n{instructions}\n\n{all_options}",
        },
        {
            "role": "assistant",
            "content": first_answer,
        },
        {
            "role": "user",
            "content": (
                "Wait — your previous answer may contradict something we "
                f"know about the user.\n\nContradiction: {contradiction}\n\n"
                "Please reconsider carefully. Re-read the persona profile "
                "and past sessions, and give your final answer (a), (b), "
                "(c), or (d) after <final_answer>."
            ),
        },
    ]

    client = _make_openai_client()
    revised_answer = first_answer
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=revision_msgs,
                max_completion_tokens=1024,
            )
            revised_answer = resp.choices[0].message.content
            break
        except Exception:  # noqa: BLE001
            time.sleep(2 ** attempt)

    return {
        "answer": revised_answer,
        "first_answer": first_answer,
        "revised": True,
        "contradiction": contradiction,
        "n_sessions_selected": len(picked),
    }




# ────────────────────────────────────────────────────────────
# Async LLM helpers (Day 2 — asyncio parallelisation)
#   Only the OpenAI/OpenRouter calls run concurrently. MemKraft
#   ingestion / search calls remain synchronous: they are CPU+SQLite
#   bound and not safe to share across coroutines.
# ────────────────────────────────────────────────────────────

def _make_async_openai_client(model: str):
    """Return an openai.AsyncOpenAI client routed via OpenRouter when
    the model id looks like a provider/<name> slug (or env hints so).
    """
    import openai
    provider = os.environ.get("MEMCRAFT_LLM_PROVIDER", "").lower()
    # codex CLI branch (Day 2.5): ChatGPT OAuth via async subprocess
    if _use_codex():
        return CodexSubprocessAsyncAdapter()
    # litellm-vhh branch (Day 3): explicit provider OR LITELLM_VHH_KEY set
    if provider == "litellm-vhh" or os.environ.get("LITELLM_VHH_KEY"):
        return openai.AsyncOpenAI(
            base_url="https://llm.vhh.sh/v1",
            api_key=os.environ.get(
                "LITELLM_VHH_KEY",
                "sk-litellm-local-58e6dff127b675454d6cc518918738974c67fb9395b47ebd",
            ),
            default_headers={"User-Agent": "OpenAI/Python 1.50.0"},
        )
    use_or = provider == "openrouter" or (
        "/" in str(model) and os.environ.get("OPENROUTER_API_KEY")
    )
    if use_or:
        return openai.AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
    return openai.AsyncOpenAI()


async def _async_chat(client, model, messages, max_retries=3):
    import asyncio
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=1024,
            )
            return resp.choices[0].message.content
        except Exception as e:  # noqa: BLE001
            last_err = e
            await asyncio.sleep(2 ** attempt)
    raise last_err or RuntimeError("async LLM query failed after retries")


async def _async_query_llm(client, question, all_options, messages, model):
    instructions = (
        "Find the most appropriate model response and give your final answer "
        "(a), (b), (c), or (d) after the special token <final_answer>."
    )
    payload = list(messages) + [
        {"role": "user", "content": f"{question}\n\n{instructions}\n\n{all_options}"}
    ]
    return await _async_chat(client, model, payload)


async def _async_query_with_validation(
    client, mk, persona_name, question, all_options,
    context, end_idx, topic, model,
    max_retries=1, max_sessions=5, qtype=None,
):
    msgs, picked = build_compressed_context(
        mk, persona_name, question, topic, context, end_idx,
        max_sessions=max_sessions, qtype=qtype,
    )
    first_answer = await _async_query_llm(
        client, question, all_options, msgs, model=model,
    )
    contradiction = fact_check_answer(
        mk, persona_name, first_answer, question, all_options,
    )
    if not contradiction or max_retries <= 0:
        return {
            "answer": first_answer,
            "first_answer": first_answer,
            "revised": False,
            "contradiction": contradiction,
            "n_sessions_selected": len(picked),
        }
    instructions = (
        "Find the most appropriate model response and give your final "
        "answer (a), (b), (c), or (d) after the special token "
        "<final_answer>."
    )
    revision_msgs = list(msgs) + [
        {"role": "user", "content": f"{question}\n\n{instructions}\n\n{all_options}"},
        {"role": "assistant", "content": first_answer},
        {
            "role": "user",
            "content": (
                "Wait — your previous answer may contradict something we "
                f"know about the user.\n\nContradiction: {contradiction}\n\n"
                "Please reconsider carefully. Re-read the persona profile "
                "and past sessions, and give your final answer (a), (b), "
                "(c), or (d) after <final_answer>."
            ),
        },
    ]
    revised_answer = first_answer
    try:
        revised_answer = await _async_chat(client, model, revision_msgs)
    except Exception:  # noqa: BLE001
        pass
    return {
        "answer": revised_answer,
        "first_answer": first_answer,
        "revised": True,
        "contradiction": contradiction,
        "n_sessions_selected": len(picked),
    }


async def _process_question_async(
    client, mk, adapter, ingestion_cache, cache_lock,
    q, contexts, variants, model, max_sessions,
    run_stats=None,
):
    import asyncio
    qid = q.get("question_id")
    qtype = q["question_type"]
    readable = QTYPE_MAP.get(qtype, qtype)
    shared_ctx_id = q["shared_context_id"]
    end_idx = int(q["end_index_in_shared_context"])
    topic = q.get("topic", "")
    question = q["user_question_or_message"]
    options = q["all_options"]
    correct = q["correct_answer"]
    ctx = contexts.get(shared_ctx_id, [])
    persona_fallback = f"persona_{q.get('persona_id','x')}"
    cache_key = f"{shared_ctx_id}::{end_idx}"
    async with cache_lock:
        if cache_key not in ingestion_cache and ctx:
            import io, contextlib
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    ing = adapter.ingest(
                        ctx, end_idx,
                        persona_name_fallback=persona_fallback,
                    )
                ingestion_cache[cache_key] = ing
                if run_stats is not None:
                    run_stats["ingestions"] = run_stats.get("ingestions", 0) + 1
                    st = ing.get("stats", {}) or {}
                    run_stats["total_statements"] = run_stats.get("total_statements", 0) + st.get("statements", 0)
                    run_stats["total_preferences"] = run_stats.get("total_preferences", 0) + st.get("preferences", 0)
                    run_stats["total_facts"] = run_stats.get("total_facts", 0) + st.get("facts", 0)
                # B1 (2026-04-25): optionally project preferences into the
                # graph layer so reasoning queries can traverse entities.
                # Opt-in via MEMCRAFT_USE_GRAPH_SYNC=1; failures are silent
                # so the validator path keeps working when the mixin is
                # absent or misbehaving.
                if os.environ.get("MEMCRAFT_USE_GRAPH_SYNC"):
                    try:
                        persona_for_sync = ing.get("persona_name") or persona_fallback
                        if hasattr(mk, "sync_all_preferences_to_graph"):
                            sync_res = mk.sync_all_preferences_to_graph(
                                persona_for_sync, include_closed=True,
                            )
                            if run_stats is not None:
                                run_stats["graph_edges_added"] = (
                                    run_stats.get("graph_edges_added", 0)
                                    + sync_res.get("edges_added", 0)
                                )
                                run_stats["graph_synced_personas"] = (
                                    run_stats.get("graph_synced_personas", 0) + 1
                                )
                    except Exception:
                        pass
            except Exception as e:  # noqa: BLE001
                ingestion_cache[cache_key] = {
                    "persona_name": persona_fallback,
                    "persona_info": {},
                    "stats": {"facts": 0, "preferences": 0, "messages": 0,
                              "sessions": 0, "statements": 0},
                    "statements": [],
                    "sessions": [],
                    "_error": f"{type(e).__name__}: {e}",
                }
        ing = ingestion_cache.get(cache_key, {
            "persona_name": persona_fallback,
            "statements": [],
        })
    persona_name = ing.get("persona_name") or persona_fallback
    out = {"qid": qid, "readable": readable, "per_variant": {}}
    coros = []
    coro_keys = []
    if "baseline" in variants:
        msgs = build_baseline_context(ctx, end_idx)
        coros.append(_async_query_llm(client, question, options, msgs, model=model))
        coro_keys.append("baseline")
    if "validator" in variants:
        coros.append(_async_query_with_validation(
            client, mk, persona_name, question, options,
            ctx, end_idx, topic, model=model,
            max_retries=1, max_sessions=max_sessions,
            qtype=qtype,
        ))
        coro_keys.append("validator")
    results = await asyncio.gather(*coros, return_exceptions=True)
    for key, res in zip(coro_keys, results):
        if isinstance(res, Exception):
            out["per_variant"][key] = {
                "correct": False,
                "error": f"{type(res).__name__}: {res}",
            }
            continue
        if key == "baseline":
            out["per_variant"][key] = {"correct": extract_answer(res, correct)}
        else:
            out["per_variant"][key] = {
                "correct": extract_answer(res["answer"], correct),
                "first_letter": _predicted_letter(res["first_answer"]),
                "final_letter": _predicted_letter(res["answer"]),
                "revised": res["revised"],
                "n_sessions_selected": res["n_sessions_selected"],
            }
    return out


async def _run_benchmark_async(
    questions, contexts, mk, adapter, variants, model, max_sessions,
    concurrency, completed_ids, results, validator_stats, run_stats,
    checkpoint_path, checkpoint_every, start_ts, split, verbose,
):
    import asyncio
    client = _make_async_openai_client(model)
    sem = asyncio.Semaphore(max(1, concurrency))
    cache_lock = asyncio.Lock()
    ingestion_cache = {}

    async def bounded(q):
        async with sem:
            return await _process_question_async(
                client, mk, adapter, ingestion_cache, cache_lock,
                q, contexts, variants, model, max_sessions,
                run_stats=run_stats,
            )

    pending = [q for q in questions if q.get("question_id") not in completed_ids]
    if verbose:
        print(f"  async mode: {len(pending)} pending, concurrency={concurrency}")
    tasks = [asyncio.create_task(bounded(q)) for q in pending]
    done_count = 0
    for fut in asyncio.as_completed(tasks):
        try:
            out = await fut
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  ⚠️ task failure: {type(e).__name__}: {e}")
            continue
        done_count += 1
        qid = out["qid"]
        readable = out["readable"]
        for variant, res in out["per_variant"].items():
            if "error" in res:
                results[variant]["errors"].append({
                    "question_id": qid,
                    "error": res["error"],
                })
            _accumulate(results[variant], readable, bool(res.get("correct")))
            if variant == "validator" and "n_sessions_selected" in res:
                validator_stats["_session_sum"] += res["n_sessions_selected"]
                validator_stats["_session_n"] += 1
                if res.get("revised"):
                    validator_stats["revisions_triggered"] += 1
                    if res.get("first_letter") != res.get("final_letter"):
                        validator_stats["revisions_changed_answer"] += 1
        completed_ids.add(qid)
        if verbose and done_count % 10 == 0:
            per_variant = " | ".join(
                f"{v}: {results[v]['correct']}/{results[v]['total']}"
                for v in variants
            )
            print(f"  [{done_count}/{len(pending)}] {per_variant}")
        if checkpoint_path and done_count % checkpoint_every == 0:
            _save_checkpoint(checkpoint_path, {
                "completed_ids": sorted(completed_ids),
                "results": results,
                "validator_stats": validator_stats,
                "run_stats": run_stats,
                "split": split,
                "model": model,
                "variants": variants,
                "elapsed_seconds": time.time() - start_ts,
            })


# ────────────────────────────────────────────────────────────
# Benchmark runner
# ────────────────────────────────────────────────────────────

def run_benchmark(
    split: str = "32k",
    max_questions: int = 0,
    variants: Optional[List[str]] = None,
    model: str = "gpt-4.1",
    max_sessions: int = 5,
    checkpoint_path: Optional[Path] = None,
    checkpoint_every: int = 10,
    verbose: bool = True,
    concurrency: int = 1,
) -> Dict[str, Any]:
    """Run baseline + validator across PersonaMem."""
    variants = variants or ["baseline", "validator"]
    valid = {"baseline", "validator"}
    for v in variants:
        if v not in valid:
            raise ValueError(
                f"Unknown variant: {v} (allowed: {sorted(valid)})"
            )

    if verbose:
        print(f"Loading PersonaMem {split}...")
    questions, contexts = load_persona_mem(split)
    if max_questions > 0:
        questions = questions[:max_questions]
    if verbose:
        print(
            f"Loaded {len(questions)} questions / {len(contexts)} contexts"
        )

    # Shared MemKraft instance (per-context ingestion cached)
    mk_dir = f"/tmp/personamem-validator-{split}-{int(time.time())}"
    mk = MemKraft(base_dir=mk_dir)
    mk.init(verbose=False)
    adapter = PersonaMemAdapter(mk)

    ingestion_cache: Dict[str, Dict[str, Any]] = {}
    results: Dict[str, Dict[str, Any]] = {v: _empty_results(v) for v in variants}

    # Extra stats for the validator
    validator_stats = {
        "revisions_triggered": 0,
        "revisions_changed_answer": 0,
        "avg_sessions_selected": 0.0,
        "_session_sum": 0,
        "_session_n": 0,
    }

    completed_ids: set = set()
    ckpt_data: Dict[str, Any] = {}
    if checkpoint_path:
        ckpt_data = _load_checkpoint(checkpoint_path)
        if ckpt_data:
            completed_ids = set(ckpt_data.get("completed_ids", []))
            for v in variants:
                if v in ckpt_data.get("results", {}):
                    results[v] = ckpt_data["results"][v]
            if "validator_stats" in ckpt_data:
                validator_stats.update(ckpt_data["validator_stats"])
            if verbose:
                print(f"Resumed checkpoint: {len(completed_ids)} completed")

    start_ts = time.time()
    run_stats = {
        "ingestions": 0,
        "total_statements": 0,
        "total_preferences": 0,
        "total_facts": 0,
    }

    # Day 2: async path when concurrency > 1
    if concurrency and concurrency > 1:
        import asyncio
        if verbose:
            print(f"Running with asyncio concurrency={concurrency}")
        asyncio.run(_run_benchmark_async(
            questions=questions,
            contexts=contexts,
            mk=mk,
            adapter=adapter,
            variants=variants,
            model=model,
            max_sessions=max_sessions,
            concurrency=concurrency,
            completed_ids=completed_ids,
            results=results,
            validator_stats=validator_stats,
            run_stats=run_stats,
            checkpoint_path=checkpoint_path,
            checkpoint_every=checkpoint_every,
            start_ts=start_ts,
            split=split,
            verbose=verbose,
        ))
        # finalize + return same as the sync path
        for v in variants:
            _finalize(results[v])
        if validator_stats["_session_n"]:
            validator_stats["avg_sessions_selected"] = (
                validator_stats["_session_sum"] / validator_stats["_session_n"]
            )
        if checkpoint_path:
            _save_checkpoint(checkpoint_path, {
                "completed_ids": sorted(completed_ids),
                "results": results,
                "validator_stats": validator_stats,
                "run_stats": run_stats,
                "split": split,
                "model": model,
                "variants": variants,
                "elapsed_seconds": time.time() - start_ts,
                "finished": True,
            })
        return {
            "split": split,
            "model": model,
            "variants": variants,
            "n_questions": len(questions),
            "n_completed": len(completed_ids),
            "max_sessions": max_sessions,
            "concurrency": concurrency,
            "run_stats": run_stats,
            "validator_stats": validator_stats,
            "results": results,
            "elapsed_seconds": time.time() - start_ts,
            "memkraft_dir": mk_dir,
        }

    for i, q in enumerate(questions):
        qid = q.get("question_id", str(i))
        if qid in completed_ids:
            continue

        qtype = q["question_type"]
        readable = QTYPE_MAP.get(qtype, qtype)
        shared_ctx_id = q["shared_context_id"]
        end_idx = int(q["end_index_in_shared_context"])
        topic = q.get("topic", "")
        question = q["user_question_or_message"]
        options = q["all_options"]
        correct = q["correct_answer"]
        ctx = contexts.get(shared_ctx_id, [])

        # Ingest into MemKraft once per (context_id, end_idx)
        cache_key = f"{shared_ctx_id}::{end_idx}"
        if cache_key not in ingestion_cache and ctx:
            import io
            import contextlib
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    ing = adapter.ingest(
                        ctx, end_idx,
                        persona_name_fallback=f"persona_{q.get('persona_id','x')}",
                    )
                ingestion_cache[cache_key] = ing
                run_stats["ingestions"] += 1
                run_stats["total_statements"] += ing["stats"]["statements"]
                run_stats["total_preferences"] += ing["stats"]["preferences"]
                run_stats["total_facts"] += ing["stats"]["facts"]
            except Exception as e:  # noqa: BLE001
                if verbose:
                    print(f"  ⚠️ ingestion failed for {shared_ctx_id}: {e}")
                ingestion_cache[cache_key] = {
                    "persona_name": f"persona_{q.get('persona_id','x')}",
                    "persona_info": {},
                    "stats": {"facts": 0, "preferences": 0, "messages": 0,
                               "sessions": 0, "statements": 0},
                    "statements": [],
                    "sessions": [],
                }

        ing = ingestion_cache.get(cache_key, {
            "persona_name": f"persona_{q.get('persona_id','x')}",
            "statements": [],
        })
        persona_name = ing.get("persona_name") or f"persona_{q.get('persona_id','x')}"

        for variant in variants:
            try:
                if variant == "baseline":
                    msgs = build_baseline_context(ctx, end_idx)
                    answer = query_llm(question, options, msgs, model=model)
                    correct_flag = extract_answer(answer, correct)
                    _accumulate(results[variant], readable, correct_flag)

                elif variant == "validator":
                    res = query_with_validation(
                        mk, persona_name, question, options,
                        ctx, end_idx, topic, model=model,
                        max_retries=1, max_sessions=max_sessions,
                        qtype=qtype,
                    )
                    correct_flag = extract_answer(res["answer"], correct)
                    _accumulate(results[variant], readable, correct_flag)

                    # Record extra validator stats
                    validator_stats["_session_sum"] += res["n_sessions_selected"]
                    validator_stats["_session_n"] += 1
                    if res["revised"]:
                        validator_stats["revisions_triggered"] += 1
                        first_letter = _predicted_letter(res["first_answer"])
                        final_letter = _predicted_letter(res["answer"])
                        if first_letter != final_letter:
                            validator_stats["revisions_changed_answer"] += 1
            except Exception as e:  # noqa: BLE001
                results[variant]["errors"].append({
                    "question_id": qid,
                    "error": f"{type(e).__name__}: {e}",
                })
                _accumulate(results[variant], readable, False)

        completed_ids.add(qid)

        if verbose and (i + 1) % 10 == 0:
            per_variant = " | ".join(
                f"{v}: {results[v]['correct']}/{results[v]['total']}"
                for v in variants
            )
            print(f"  [{i+1}/{len(questions)}] {per_variant}")

        if checkpoint_path and (i + 1) % checkpoint_every == 0:
            _save_checkpoint(checkpoint_path, {
                "completed_ids": sorted(completed_ids),
                "results": results,
                "validator_stats": validator_stats,
                "run_stats": run_stats,
                "split": split,
                "model": model,
                "variants": variants,
                "elapsed_seconds": time.time() - start_ts,
            })

    # Finalize
    for v in variants:
        _finalize(results[v])
    if validator_stats["_session_n"]:
        validator_stats["avg_sessions_selected"] = (
            validator_stats["_session_sum"] / validator_stats["_session_n"]
        )

    if checkpoint_path:
        _save_checkpoint(checkpoint_path, {
            "completed_ids": sorted(completed_ids),
            "results": results,
            "validator_stats": validator_stats,
            "run_stats": run_stats,
            "split": split,
            "model": model,
            "variants": variants,
            "elapsed_seconds": time.time() - start_ts,
            "finished": True,
        })

    return {
        "split": split,
        "model": model,
        "variants": variants,
        "n_questions": len(questions),
        "n_completed": len(completed_ids),
        "max_sessions": max_sessions,
        "run_stats": run_stats,
        "validator_stats": validator_stats,
        "results": results,
        "per_question": per_question,
        "elapsed_seconds": time.time() - start_ts,
        "memkraft_dir": mk_dir,
    }


# ────────────────────────────────────────────────────────────
# Reporting
# ────────────────────────────────────────────────────────────

def print_report(run: Dict[str, Any]) -> None:
    variants = run["variants"]
    results = run["results"]

    print("\n" + "=" * 72)
    print(
        f"PersonaMem Validator Harness | split={run['split']} | "
        f"model={run['model']} | max_sessions={run['max_sessions']}"
    )
    print(
        f"Questions: {run['n_completed']}/{run['n_questions']} | "
        f"elapsed: {run['elapsed_seconds']:.1f}s"
    )
    rs = run.get("run_stats", {})
    if rs:
        print(
            f"Ingestions: {rs.get('ingestions', 0)} | "
            f"statements: {rs.get('total_statements', 0)} | "
            f"preferences: {rs.get('total_preferences', 0)} | "
            f"facts: {rs.get('total_facts', 0)}"
        )
    vs = run.get("validator_stats", {})
    if vs:
        print(
            f"Validator: avg sessions selected = "
            f"{vs.get('avg_sessions_selected', 0):.2f} | "
            f"revisions triggered = {vs.get('revisions_triggered', 0)} | "
            f"revisions that changed answer = "
            f"{vs.get('revisions_changed_answer', 0)}"
        )
    print("=" * 72)

    print(
        f"\n{'Variant':<14} {'Accuracy':>10} {'Correct':>10} "
        f"{'Total':>8} {'Errors':>8}"
    )
    print("-" * 54)
    for v in variants:
        r = results[v]
        print(
            f"{v:<14} {r['accuracy']:>9.1f}% {r['correct']:>10} "
            f"{r['total']:>8} {len(r['errors']):>8}"
        )

    all_types: set = set()
    for v in variants:
        all_types.update(results[v]["by_type"].keys())

    if all_types:
        header = f"\n{'Query Type':<36}"
        for v in variants:
            header += f" {v:>12}"
        print(header)
        print("-" * (36 + 13 * len(variants)))
        for qt in sorted(all_types):
            row = f"{qt:<36}"
            for v in variants:
                bt = results[v]["by_type"].get(qt, {})
                acc = bt.get("accuracy")
                if acc is None:
                    row += f" {'--':>12}"
                else:
                    row += f" {acc:>11.1f}%"
            print(row)

    if "baseline" in variants and "validator" in variants:
        b_acc = results["baseline"]["accuracy"]
        v_acc = results["validator"]["accuracy"]
        d = v_acc - b_acc
        sign = "+" if d >= 0 else ""
        arrow = "📈" if d > 0 else ("📉" if d < 0 else "➡️")
        print(f"\n{arrow} validator vs baseline: {sign}{d:.1f}%")


# ────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PersonaMem Benchmark — Validator Variant"
    )
    parser.add_argument("--split", default="32k", choices=["32k", "128k", "1M"])
    parser.add_argument(
        "--max-questions", type=int, default=0,
        help="Limit number of questions (0 = all)",
    )
    parser.add_argument("--model", default="gpt-4.1")
    parser.add_argument(
        "--variants", default="baseline,validator",
        help="Comma-separated subset of {baseline,validator}",
    )
    parser.add_argument(
        "--max-sessions", type=int, default=5,
        help="Max sessions to pick for validator (Pass 1 retrieval)",
    )
    parser.add_argument(
        "--checkpoint", default=None,
        help="Path to checkpoint file (resume + save progress)",
    )
    parser.add_argument("--checkpoint-every", type=int, default=10)
    parser.add_argument(
        "--out", default=None,
        help="Path to save final JSON results",
    )
    parser.add_argument(
        "--concurrency", type=int, default=1,
        help="Number of questions processed in parallel (asyncio). "
             "1 = legacy sync loop. 5\u201310 recommended for OpenRouter.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]

    ckpt = Path(args.checkpoint) if args.checkpoint else None
    if ckpt:
        ckpt.parent.mkdir(parents=True, exist_ok=True)

    run = run_benchmark(
        split=args.split,
        max_questions=args.max_questions,
        variants=variants,
        model=args.model,
        max_sessions=args.max_sessions,
        checkpoint_path=ckpt,
        checkpoint_every=args.checkpoint_every,
        verbose=not args.quiet,
        concurrency=args.concurrency,
    )

    print_report(run)

    out_path = args.out or (
        f"{HERE}/results_validator_{args.split}_{int(time.time())}.json"
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(run, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
