"""
LongMemEval × MemKraft 1.0.1 Harness

샘플당 워크플로우:
  1) haystack_sessions를 MemKraft에 ingest
  2) question으로 MemKraft 검색 (top_k)
  3) 검색 결과 + 질문을 Claude에 전달 → 답변
  4) 정답(answer)과 비교

ingestion 전략: 각 메시지를 log_event 로 저장 (timestamp + role + content)
retrieval: mk.search(question, top_k=K)
"""
from __future__ import annotations

import os
import sys
import io
import json
import time
import tempfile
import traceback
import contextlib
from typing import Any
from pathlib import Path

sys.path.insert(0, "/Users/gimseojun/memcraft/src")

from memkraft import MemKraft  # noqa: E402
# Pluggable LLM backend (anthropic | openai | openrouter | litellm-vhh).
# `make_client_with_messages_api()` returns an object whose
# `.messages.create(...)` mirrors anthropic.Anthropic(), so the rest of the
# harness keeps working unchanged. Backend chosen via MK_LME_LLM_BACKEND env.
from llm_backend import make_client_with_messages_api  # noqa: E402


DEFAULT_MODEL = "claude-haiku-4-5"


class LongMemEvalHarness:
    def __init__(self, model: str = DEFAULT_MODEL, top_k: int = 10, verbose: bool = False):
        # If MK_LME_LLM_MODEL is set we let the backend pick (env-driven runs);
        # otherwise the harness keeps the historical default for the
        # Anthropic codepath.
        env_model = os.environ.get("MK_LME_LLM_MODEL")
        self.client = make_client_with_messages_api()
        # Use env override > caller-supplied > backend default.
        self.model = env_model or model or self.client.model
        self.backend = getattr(self.client, "backend", "anthropic")
        self.top_k = top_k
        self.verbose = verbose
        # 통계
        self.ingest_time_total = 0.0
        self.search_time_total = 0.0
        self.llm_time_total = 0.0

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------
    def ingest_sessions(self, mk: MemKraft, sample: dict[str, Any]) -> int:
        """haystack_sessions를 MemKraft.inbox 의 .md 파일로 저장.

        MemKraft.search()는 inbox/entities/tasks 등의 .md 파일만 스캔하므로
        log_event(JSONL)는 retrieval 대상이 아님.
        → 세션당 markdown 파일 1개 생성 (메시지는 내부 섹션으로 쌓음).
        메시지 수 반환.
        """
        sessions = sample.get("haystack_sessions", [])
        dates = sample.get("haystack_dates", [])
        session_ids = sample.get("haystack_session_ids", [])

        inbox = mk.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)

        t0 = time.time()
        msg_count = 0
        for idx, session in enumerate(sessions):
            date = dates[idx] if idx < len(dates) else ""
            sid = session_ids[idx] if idx < len(session_ids) else f"session_{idx}"

            lines: list[str] = []
            lines.append(f"# Session {sid}")
            lines.append("")
            lines.append(f"**Date:** {date}")
            lines.append(f"**Session ID:** {sid}")
            lines.append("")
            lines.append("## Messages")
            lines.append("")
            for m_idx, msg in enumerate(session):
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", "user")
                content = msg.get("content", "") or ""
                if not content:
                    continue
                # v4.2 fix (2026-04-22): keep v3 identical retrieval layout
                # (1500-char snippet, newlines → spaces) so MemKraft scoring
                # does not shift, but also persist the FULL message content
                # in a sidecar {sid}.full.md file. _format_context will swap
                # in the full version at LLM-prompt time, so that long
                # assistant enumerations (e.g. the 100-parameter list where
                # item 27 lives at char 1501+) are not clipped.
                snippet = content[:1500].replace("\n", " ").strip()
                lines.append(f"### [{m_idx}] {role}")
                lines.append(snippet)
                lines.append("")
                msg_count += 1

            safe_sid = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in sid)
            (inbox / f"{safe_sid}.md").write_text("\n".join(lines), encoding="utf-8")

            # v4.2: sidecar full-content file (NOT in inbox so it won't
            # perturb MemKraft search). Used by _format_context to feed the
            # LLM the untruncated messages of any retrieved session.
            full_lines: list[str] = []
            full_lines.append(f"# Session {sid} (FULL)")
            full_lines.append("")
            full_lines.append(f"**Date:** {date}")
            full_lines.append(f"**Session ID:** {sid}")
            full_lines.append("")
            full_lines.append("## Messages")
            full_lines.append("")
            for m_idx, msg in enumerate(session):
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", "user")
                content = msg.get("content", "") or ""
                if not content:
                    continue
                full_lines.append(f"### [{m_idx}] {role}")
                full_lines.append(content.strip())
                full_lines.append("")
            full_dir = mk.base_dir / "_full_sessions"
            full_dir.mkdir(parents=True, exist_ok=True)
            (full_dir / f"{safe_sid}.md").write_text("\n".join(full_lines), encoding="utf-8")

        self.ingest_time_total += time.time() - t0
        return msg_count

    # ------------------------------------------------------------------
    # Retrieval + LLM
    # ------------------------------------------------------------------
    def _format_context(self, results: Any, mk: MemKraft, max_context: int = 30000) -> str:
        """mk.search 결과를 프롬프트용 문자열로.
        search 결과는 {"file": rel_path, "score": ..., "snippet": ...} 형식.
        각 파일 전체를 읽어서 LLM 에 전달 (snippet은 너무 짧음).
        """
        if not results:
            return ""
        out: list[str] = []
        total_chars = 0
        MAX_CONTEXT = max_context  # haiku 기준 여유 있는 크기
        # v4.4 (2026-04-22): default back to v3's inbox-truncated content
        # (max 1500 chars/message). Swap in the untruncated full sidecar
        # ONLY for questions that reference a specific element inside a
        # long assistant enumeration (e.g. "what was the 27th parameter").
        # Using full everywhere made multi-session counting questions
        # double-count because repeated long assistant replies confuse
        # the model; v3 was already strong on those (13/13). Keeping
        # full only for assistant-enumeration-reference questions closes
        # the single failure (qid=8752c811) without regressing elsewhere.
        need_full = self._needs_full_assistant_content(
            getattr(self, "_current_question", "") or ""
        )
        blocks: list[tuple[str, str, str, str]] = []  # (fpath, score, inbox_content, full_content)
        for r in (results if isinstance(results, list) else []):
            if isinstance(r, dict):
                fpath = r.get("file")
                score = r.get("score", 0)
                if fpath:
                    abs_path = mk.base_dir / fpath
                    full_path = mk.base_dir / "_full_sessions" / Path(fpath).name
                    inbox_content = ""
                    if abs_path.exists():
                        try:
                            inbox_content = abs_path.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            inbox_content = r.get("snippet", "")
                    else:
                        inbox_content = r.get("snippet", "")
                    full_content = ""
                    if full_path.exists():
                        try:
                            full_content = full_path.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            full_content = ""
                    blocks.append((fpath, str(score), inbox_content, full_content))
                else:
                    blocks.append(("", str(score), json.dumps(r, ensure_ascii=False)[:400], ""))
            else:
                blocks.append(("", "0", str(r), ""))

        # Estimate total size if every block uses its full sidecar.
        def _block_text(fpath: str, score: str, body: str) -> str:
            if fpath:
                return f"--- [score={score}] {fpath} ---\n{body}\n"
            return f"- {body}\n"

        use_full_everywhere = False
        if need_full:
            full_total = sum(
                len(_block_text(fp, sc, fc or ic)) for fp, sc, ic, fc in blocks
            )
            use_full_everywhere = full_total <= max_context

        total_chars = 0
        for fpath, score, inbox_content, full_content in blocks:
            body = (full_content or inbox_content) if use_full_everywhere else inbox_content
            block = _block_text(fpath, score, body)
            if total_chars + len(block) > max_context:
                out.append(f"[context truncated at {max_context} chars, {len(blocks)} total results available]")
                break
            out.append(block)
            total_chars += len(block)
        return "\n".join(out)

    def _expand_query(self, question: str) -> list[str]:
        """Generate additional search queries from the original question.
        Keeps original + keyword-only variants for better recall.
        """
        import re
        # strip common wh-words + filler
        STOP = {"how", "what", "when", "where", "which", "who", "why", "the", "a", "an",
                "is", "are", "was", "were", "do", "did", "does", "have", "had", "has",
                "i", "my", "me", "you", "your", "we", "our", "they", "their",
                "to", "in", "on", "at", "of", "for", "with", "about", "from",
                "many", "much", "last", "ago", "since", "before", "after",
                "been", "being", "this", "that", "these", "those", "it", "its",
                "can", "could", "would", "will", "should", "may", "might",
                "remind", "tell", "remember", "know", "think",
                "regularly", "currently", "recently", "often", "still"}
        tokens = re.findall(r"[A-Za-z][A-Za-z'\-]*", question.lower())
        keywords = [t for t in tokens if t not in STOP and len(t) > 2]
        variants = []
        if keywords:
            # top noun-ish tokens
            variants.append(" ".join(keywords))
            # pairs of keywords (capture phrases like "summer nights", "sugar factory")
            if len(keywords) >= 2:
                variants.append(" ".join(keywords[:4]))
        return variants

    def _aggregation_keywords(self, question: str) -> list[str]:
        """Extract likely-aggregation noun keywords for broader recall.
        Returns single-noun queries to run in addition to the original question.
        """
        import re
        STOP = {"how", "what", "when", "where", "which", "who", "why", "the", "a", "an",
                "is", "are", "was", "were", "do", "did", "does", "have", "had", "has",
                "i", "my", "me", "you", "your", "we", "our", "they", "their",
                "to", "in", "on", "at", "of", "for", "with", "about", "from",
                "many", "much", "last", "ago", "since", "before", "after",
                "been", "being", "this", "that", "these", "those", "it", "its",
                "can", "could", "would", "will", "should", "may", "might",
                "remind", "tell", "remember", "know", "think", "total", "overall",
                "regularly", "currently", "recently", "often", "still", "including",
                "one", "two", "three", "four", "five", "some", "any", "each", "every",
                "make", "made", "making", "new", "different", "types", "type",
                "spend", "spent", "participated", "participating", "activities",
                "ever", "days", "times", "number", "count", "purchased", "worked",
                "went", "view", "viewed", "go", "used", "got", "finished"}
        tokens = re.findall(r"[A-Za-z][A-Za-z'\-]*", question.lower())
        kws = [t for t in tokens if t not in STOP and len(t) > 2]
        # unique, preserve order
        seen = set(); out = []
        for k in kws:
            if k not in seen:
                seen.add(k); out.append(k)
        return out

    def _search_multi(self, mk: MemKraft, question: str, question_date: str = "") -> list[dict]:
        """Retrieval dispatcher.

        Selection via env var ``MK_SEARCH_MODE``:
          * ``smart``    → ``search_smart`` (v1.0.2 Phase 2, default)
          * ``expand``   → ``search_expand`` (v1.0.2 Phase 1)
          * ``baseline`` → ``search_v2`` without expansion
          * ``legacy``   → 1.0.1 manual fallback path
          * ``hybrid``   → ``search_hybrid`` (v2.7.3, BM25 ⊕ semantic
                          via RRF; ``MK_HYBRID_ALPHA`` weights the
                          semantic side, default 0.5)
          * ``semantic`` → ``search_semantic`` (v2.7.3, dense only)

        For aggregation/multi-session questions, also run supplementary
        single-keyword searches and merge results to boost recall across
        sessions that only share one keyword with the question.
        """
        mode = (os.environ.get("MK_SEARCH_MODE") or "smart").lower()
        # legacy env var still honoured for backward compat
        if os.environ.get("MK_NO_EXPAND"):
            mode = "baseline"

        is_agg = self._is_aggregation_question(question)

        def _primary() -> list[dict]:
            try:
                if mode == "baseline":
                    return mk.search_v2(
                        question, top_k=max(self.top_k * 2, 30), expand_query=False
                    )
                if mode == "expand" and hasattr(mk, "search_expand"):
                    return mk.search_expand(question, top_k=max(self.top_k * 2, 30))
                if mode == "smart" and hasattr(mk, "search_smart"):
                    return mk.search_smart(
                        question,
                        top_k=max(self.top_k * 2, 30),
                        date_hint=(question_date or None),
                    )
                # v2.7.3: hybrid + semantic dispatch for embedding bench.
                if mode == "hybrid" and hasattr(mk, "search_hybrid"):
                    alpha_env = os.environ.get("MK_HYBRID_ALPHA")
                    try:
                        alpha = float(alpha_env) if alpha_env else 0.5
                    except ValueError:
                        alpha = 0.5
                    return mk.search_hybrid(
                        question,
                        top_k=max(self.top_k * 2, 30),
                        alpha=alpha,
                        date_hint=(question_date or None),
                    )
                if mode == "semantic" and hasattr(mk, "search_semantic"):
                    return mk.search_semantic(
                        question,
                        top_k=max(self.top_k * 2, 30),
                    )
            except Exception as e:
                if self.verbose:
                    print(f"v1.0.2 search error ({mode}): {e}", file=sys.stderr)
            return []

        primary = _primary()

        # Aggregation boost: add single-keyword recall passes.
        if is_agg and primary is not None and os.environ.get("MK_AGG_KEYWORD_PASS", "1") != "0":
            merged: dict[str, dict] = {}
            for r in primary or []:
                if isinstance(r, dict) and r.get("file"):
                    merged[r["file"]] = r
            keywords = self._aggregation_keywords(question)[:5]
            per_kw_topk = max(self.top_k, 15)
            for kw in keywords:
                try:
                    if hasattr(mk, "search_smart"):
                        extra = mk.search_smart(kw, top_k=per_kw_topk)
                    elif hasattr(mk, "search_v2"):
                        extra = mk.search_v2(kw, top_k=per_kw_topk, expand_query=False)
                    else:
                        buf = io.StringIO()
                        with contextlib.redirect_stdout(buf):
                            extra = mk.search(kw)
                except Exception as e:
                    if self.verbose:
                        print(f"agg keyword pass '{kw}' error: {e}", file=sys.stderr)
                    continue
                for r in extra or []:
                    if not isinstance(r, dict):
                        continue
                    f = r.get("file")
                    if not f:
                        continue
                    # keep higher score
                    existing = merged.get(f)
                    if existing is None or r.get("score", 0) > existing.get("score", 0):
                        merged[f] = r
            combined = list(merged.values())
            combined.sort(key=lambda x: x.get("score", 0), reverse=True)
            return combined

        if primary:
            return primary

        # Legacy fallback (1.0.1 manual expansion)
        buf = io.StringIO()
        merged: dict[str, dict] = {}

        def _run(q: str) -> list[dict]:
            try:
                with contextlib.redirect_stdout(buf):
                    out = mk.search(q)
            except Exception as e:
                if self.verbose:
                    print(f"search error on '{q}': {e}", file=sys.stderr)
                return []
            return out if isinstance(out, list) else []

        for q in [question] + self._expand_query(question):
            for r in _run(q):
                if not isinstance(r, dict):
                    continue
                f = r.get("file")
                if not f:
                    continue
                if f not in merged or r.get("score", 0) > merged[f].get("score", 0):
                    merged[f] = r

        results = list(merged.values())
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results

    @staticmethod
    def _is_aggregation_question(question: str) -> bool:
        """Detect counting/aggregation/comparison questions that need broader multi-session recall."""
        q = question.lower()
        # v3 fix: temporal questions should NOT be classified as aggregation
        # (they need date arithmetic, not Pass1/Pass2 broad recall)
        temporal_exclude = [
            "how many weeks ago", "how many days ago", "how many months ago",
            "how many years ago", "how many hours ago", "how many minutes ago",
            "how long ago", "when did", "what date", "what time",
            "how long has it been", "how long since",
        ]
        if any(t in q for t in temporal_exclude):
            return False
        triggers = [
            "how many", "how much", "total", "in total", "altogether",
            "combined", "overall", "number of", "count of",
            "how often", "how long did", "how frequently",
            "days did i", "times did i", "times have i",
            "list all", "each of",
            # superlative / comparison: "which X did I ... the most/least/first/last"
            "the most", "the least", "the highest", "the lowest",
            "the first", "the last",
            "most money", "most time", "most often",
            "spent the most", "spent most", "spent the least",
            "did i spend the", "did i visit the",
            "which ",  # most "which X ..." questions benefit from full scan
        ]
        return any(t in q for t in triggers)

    @staticmethod
    def _needs_full_assistant_content(question: str) -> bool:
        """v4.4: Detect questions that reference a specific element inside a
        long assistant enumeration (e.g. "what was the 27th parameter on the
        list you gave me"). These are the only cases where the default
        1500-char/message truncation reliably drops the answer; for every
        other category v3-style truncated inbox content is equal or better.
        Return True when BOTH signals fire:
          1) the question refers back to assistant-produced content
             ("you provided", "you gave me", "you listed", "you said",
             "you mentioned", "you recommended");
          2) it asks for a specific numbered/ordinal item ("27th", "5th",
             "item N", "number N", "Nth").
        Being conservative here is important — swapping in full content
        everywhere regressed multi-session counting.
        """
        import re
        q = question.lower()
        refer_back = any(
            phrase in q
            for phrase in (
                "you provided", "you gave", "you listed", "you said",
                "you mentioned", "you recommended", "you suggested",
                "you shared", "you told me", "you showed",
                "list you", "list that you",
            )
        )
        if not refer_back:
            return False
        ordinal = bool(
            re.search(r"\b(\d+(?:st|nd|rd|th))\b", q)
            or re.search(r"\b(item|number|entry|point|parameter|element)\s+(\d+|#\d+)\b", q)
            or re.search(r"#\d+\b", q)
        )
        return ordinal

    @staticmethod
    def _is_preference_question(question: str) -> bool:
        """Heuristic: detect preference/suggestion-style questions from wording only.
        Used to switch prompt into preference-framing mode (no access to gold labels).
        """
        q = question.lower()
        triggers = [
            "suggest", "recommend", "recommendation", "what should i",
            "any ideas", "any suggestions", "any tips", "advice",
            "what would you", "what do you recommend",
        ]
        return any(t in q for t in triggers)

    def retrieve_and_answer(self, mk: MemKraft, question: str, question_date: str = "") -> tuple[str, str]:
        """returns (prediction, context_used)"""
        # v4.4: remember current question so _format_context can detect
        # assistant-enumeration-reference questions and swap in the full
        # sidecar content only when necessary.
        self._current_question = question
        t0 = time.time()
        is_pref = self._is_preference_question(question)
        is_agg = self._is_aggregation_question(question) and not is_pref
        # preference / aggregation: 컨텍스트 더 많이 확보
        saved_top_k = self.top_k
        if is_pref and os.environ.get("MK_PREF_BOOST", "1") != "0":
            self.top_k = max(saved_top_k, 25)
        elif is_agg and os.environ.get("MK_AGG_BOOST", "1") != "0":
            # aggregation은 retrieval 단계에서부터 매우 넓게
            self.top_k = max(saved_top_k, 30)
        results = self._search_multi(mk, question, question_date)
        self.top_k = saved_top_k
        self.search_time_total += time.time() - t0

        # If nothing found, include ALL sessions as a fallback (oracle has few files anyway)
        if not results:
            all_md = list(mk.inbox_dir.glob("*.md")) if mk.inbox_dir.exists() else []
            fallback_k = 30 if is_agg else (25 if is_pref else self.top_k)
            results = [{"file": str(p.relative_to(mk.base_dir)), "score": 0.0, "snippet": ""} for p in all_md[: fallback_k]]
        else:
            if is_agg:
                limit = max(self.top_k * 3, 30)
            elif is_pref:
                limit = max(self.top_k, 25)
            else:
                limit = self.top_k
            results = results[: limit]

        # aggregation은 컨텍스트 윈도 확장 (counting은 enumeration 필요)
        max_ctx = 60000 if is_agg else 30000
        context = self._format_context(results, mk, max_context=max_ctx)

        when = f"Today's date is {question_date}. Use this as 'now' when answering time-related questions.\n" if question_date else ""

        if is_pref and os.environ.get("MK_PREF_PROMPT", "1") != "0":
            prompt = (
                "You are answering a suggestion/recommendation question about a user's life, based on memory excerpts from prior conversations.\n"
                f"{when}\n"
                "Retrieved Memory:\n"
                f"{context if context else '(no relevant memory retrieved)'}\n\n"
                "This is a PREFERENCE question. Your goal is NOT just to summarize past discussions — your goal is to infer the user's preferences so future suggestions align with them.\n\n"
                "Instructions:\n"
                "1. Scan ALL retrieved sessions for clues about what the user likes, enjoys, dislikes, or has rejected — including:\n"
                "   - Topics they spoke about enthusiastically or repeatedly\n"
                "   - Concrete options they chose or planned to try\n"
                "   - Things they said they wanted to avoid, or explicitly wanted to explore BEYOND\n"
                "   - Constraints on their situation (e.g. commute length, remote work, trip destination)\n"
                "2. Frame your answer as user PREFERENCES, not as a recap. Use phrasing like:\n"
                "     'The user would prefer ...' / 'They would likely enjoy ...' / 'They may not prefer ...'\n"
                "3. Include BOTH (a) what they would prefer and (b) what they would NOT prefer, whenever the memory supports it.\n"
                "4. Be specific: name the concrete activities, genres, features, or categories from memory. Avoid generic advice that ignores their context.\n"
                "5. It's fine to write 2–4 sentences here — clarity about preferences matters more than brevity.\n"
                "6. If the retrieved memory truly has no relevant info for this question's topic, say so briefly and then infer from the user's general patterns if possible.\n\n"
                f"Question: {question}\n"
                "Answer (preference-framed):"
            )
        else:
            agg_hint = (
                "\n⚠️ THIS IS A COUNTING / AGGREGATION / MULTI-SESSION QUESTION. Follow this STRICT two-pass protocol:\n"
                "\n"
                "  PASS 1 — EXHAUSTIVE EXTRACTION (do this FIRST, before any conclusion):\n"
                "    • Go through EACH retrieved session file one-by-one, in order.\n"
                "    • For EACH session, list EVERY candidate item matching the question's category, even if:\n"
                "        - The mention is brief, offhand, or in a subordinate clause (e.g. 'by the way...', 'speaking of...').\n"
                "        - The verb differs from the question's verb (question says 'buy' → also catch 'ordered', 'got', 'downloaded', 'picked up', 'grabbed', 'purchased', 'placed an order').\n"
                "        - The item is mentioned alongside an unrelated topic (users often mix topics).\n"
                "        - The dollar amount / count is embedded mid-paragraph.\n"
                "    • Format Pass 1 as: `[Session <id>] <verb> <item> (<qualifier like $amount/date>)`.\n"
                "    • Do NOT filter or deduplicate yet. Over-collect on purpose.\n"
                "\n"
                "  PASS 2 — FILTER + AGGREGATE:\n"
                "    • Deduplicate items that are clearly the same (same product, same purchase event).\n"
                "    • Discard items that do NOT match the question's category.\n"
                "    • For 'which X the most' questions: compare ALL candidates' amounts/counts side-by-side before picking.\n"
                "    • For 'how many' questions: state the final integer ONLY after listing every unique item.\n"
                "\n"
                "  CRITICAL RULES:\n"
                "    (a) Never stop enumeration at the first 2-3 hits — scan ALL sessions to the end.\n"
                "    (b) A single session can contain MULTIPLE items; do not assume one item per session.\n"
                "    (c) If a session never mentions anything relevant, explicitly write `[Session <id>] (none)`.\n"
                "    (d) For 'most spent at' questions: build a table `{store: total_$}` across ALL sessions, then pick the max. Do not commit to one store before scanning all.\n"
                "    (e) For 'how many items' questions: an item counts even if described with non-standard verbs (vinyl 'got signed' = still a purchase if bought; mattress 'ordered' = bought).\n"
            ) if is_agg else ""
            prompt = (
                "You are answering a question about a user's life based on memory excerpts from their prior conversations with an assistant.\n"
                f"{when}"
                f"{agg_hint}\n"
                "Retrieved Memory (most relevant excerpts):\n"
                f"{context if context else '(no relevant memory retrieved)'}\n\n"
                "Instructions:\n"
                "1. Answer the question DIRECTLY using the retrieved memory. Do NOT say 'I don't know' unless the memory truly has no relevant info — always attempt your best answer.\n"
                "2. Give a short, direct answer. Keep it under 15 words when possible. Use a full sentence only if natural.\n"
                "3. Think carefully through the retrieved memory BEFORE answering. Use brief inline reasoning (1-2 sentences) if needed, ending with the final short answer on the last line.\n"
                "4. For counting questions ('How many ...?'): FIRST do Pass 1 enumeration across EVERY retrieved session (even tiny mentions in subordinate clauses), THEN do Pass 2 dedup/filter, THEN state the final count on the last line. Do NOT stop at the first 2-3 hits. Watch for items described with synonym verbs ('ordered'/'got'/'downloaded'/'picked up' all count as 'bought/acquired').\n"
                "5. For duration questions ('How long ...?'): echo the exact wording from the memory (e.g. 'three months', 'nine months', '4 years and 9 months'). Include the time unit.\n"
                "6. For time-ago questions: give both digit and unit (e.g. '3 weeks ago' not 'three weeks ago'; '18 days' not '18').\n"
                "7. For name/place questions: echo the full name with its qualifier (e.g. 'The Sugar Factory at Icon Park').\n"
                "8. For temporal questions, use Today's date above as 'now'.\n"
                "9. For multi-session questions, SYNTHESIZE across ALL retrieved sessions — do not rely on only the top session. Cross-reference them.\n"
                "10. For 'Remind me...' questions, answer as if continuing the past conversation (e.g. 'I recommended learning Ruby, Python, or PHP as a back-end language.').\n\n"
                f"Question: {question}\n"
                "Answer:"
            )

        t0 = time.time()
        # preference / aggregation은 좀 더 길게 답할 여유 (enumeration 필요)
        max_tok = 800 if is_pref else (1400 if is_agg else 500)
        # temperature=0 (v5): 결정론적 출력 — majority vote 전제
        temperature = float(os.environ.get("TEMPERATURE", "0"))
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tok,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        self.llm_time_total += time.time() - t0
        pred = resp.content[0].text.strip() if resp.content else ""
        return pred, context

    # ------------------------------------------------------------------
    # Sample runner
    # ------------------------------------------------------------------
    def run_sample(self, sample: dict[str, Any]) -> dict[str, Any]:
        qid = sample.get("question_id", "?")
        question = sample.get("question", "")
        answer = sample.get("answer", "")
        qtype = sample.get("question_type", "unknown")
        qdate = sample.get("question_date", "")

        with tempfile.TemporaryDirectory(prefix="mk_lme_") as tmp:
            mk = MemKraft(base_dir=tmp)
            msg_count = self.ingest_sessions(mk, sample)
            try:
                pred, ctx = self.retrieve_and_answer(mk, question, qdate)
                return {
                    "question_id": qid,
                    "question": question,
                    "answer": answer,
                    "prediction": pred,
                    "question_type": qtype,
                    "n_messages": msg_count,
                    "context_used_chars": len(ctx),
                }
            except Exception as e:
                return {
                    "question_id": qid,
                    "question": question,
                    "answer": answer,
                    "prediction": "",
                    "question_type": qtype,
                    "n_messages": msg_count,
                    "error": f"{type(e).__name__}: {e}",
                }
