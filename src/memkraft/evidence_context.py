"""Provenance-preserving, query-focused evidence context compilation."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .context_compiler import estimate_tokens


_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)
# Keep this deliberately small and language-specific: it only removes English
# grammatical glue from evidence anchors. If nothing remains, tokenization
# falls back to the full query so non-English and terse queries retain recall.
_ENGLISH_FUNCTION_TOKENS = {
    "a", "an", "and", "are", "at", "be", "by", "can", "could", "did", "do",
    "does", "for", "from", "had", "has", "have", "how", "i", "if", "in", "is",
    "it", "me", "my", "of", "on", "or", "our", "should", "that", "the", "their",
    "them", "there", "these", "they", "this", "to", "was", "we", "were", "what",
    "when", "where", "which", "who", "why", "with", "would", "you", "your",
}
_NUMERIC_INTENT_TOKENS = {
    "age", "count", "how", "long", "many", "much", "number", "old", "time", "total",
    "years",
}
_NUMBER_RE = re.compile(r"\d")
_SOURCE_DATE_RE = re.compile(
    r"(?im)^\s*\*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?)"
    r"(?=$|[ \t]+[A-Za-z])"
)
_LONGMEMEVAL_SOURCE_DATE_RE = re.compile(
    r"(?im)^\s*\*\*Date:\*\*\s*(\d{4}/\d{2}/\d{2})\s+"
    r"\(([A-Za-z]{3})\)\s+(\d{2}:\d{2})\s*$"
)
_LATEST_TOKENS = {"currently", "latest", "newest", "now", "recent"}
_PAST_TOKENS = {
    "before", "earlier", "former", "formerly", "initial", "oldest", "past", "previous",
    "previously",
}
_COMPARE_TOKENS = {"change", "changed", "compare", "compared", "difference", "evolve", "evolved"}
_TEMPORAL_CONTROL_TOKENS = (
    _LATEST_TOKENS | _PAST_TOKENS | _COMPARE_TOKENS | {"current", "history"}
)
_ELECTRICAL_CURRENT_TOKENS = {
    "amp", "amps", "ampere", "amperes", "amperage", "charger", "circuit", "consumption",
    "draw", "electrical", "rating", "voltage", "watt", "watts",
}
_CURRENT_TRANSFER_TOKENS = {"delivers", "flow", "flows", "output", "outputs", "provide", "provides"}


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _query_tokens(query: str) -> List[str]:
    tokens = list(dict.fromkeys(token.lower() for token in _TOKEN_RE.findall(query)))
    anchors = [
        token for token in tokens
        if token not in _ENGLISH_FUNCTION_TOKENS
        and token not in _TEMPORAL_CONTROL_TOKENS
    ]
    if anchors:
        return anchors
    # Temporal control words steer ordering but never establish relevance.
    # Preserve the historical fallback only for non-temporal terse queries.
    return [] if tokens and all(
        token in _ENGLISH_FUNCTION_TOKENS or token in _TEMPORAL_CONTROL_TOKENS
        for token in tokens
    ) else tokens


def _has_numeric_intent(query: str) -> bool:
    """Whether a question plausibly requires composing a numeric fact."""
    tokens = {token.lower() for token in _TOKEN_RE.findall(query)}
    return bool(tokens & _NUMERIC_INTENT_TOKENS)


def _temporal_intent(query: str) -> str:
    """Classify generic temporal wording without benchmark-specific cases."""
    tokens = {token.lower() for token in _TOKEN_RE.findall(query)}
    lowered = query.lower()
    # Electrical uses of "current" describe a measured quantity, not recency.
    electrical_quantity = (
        bool(tokens & _ELECTRICAL_CURRENT_TOKENS)
        or ("how much" in lowered and bool(tokens & _CURRENT_TRANSFER_TOKENS))
    )
    if "current" in tokens and electrical_quantity:
        return "neutral"
    if tokens & _COMPARE_TOKENS or "over time" in lowered or "then and now" in lowered:
        return "compare"
    if tokens & _LATEST_TOKENS or "most recent" in lowered:
        return "latest"
    # ``current`` is temporal as an adjective ("current status"), but is also
    # a common electrical noun ("what current does ... provide").
    if "current" in tokens and not re.search(r"\bwhat\s+current\b", lowered):
        return "latest"
    if tokens & _PAST_TOKENS:
        return "past"
    return "neutral"


def _parse_source_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    candidate = value.strip().replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _source_time(hit: Dict[str, Any], content: str) -> Optional[datetime]:
    """Prefer explicit validity metadata, then a validated Markdown source date."""
    explicit = _parse_source_time(hit.get("valid_from"))
    if explicit is not None:
        return explicit
    match = _SOURCE_DATE_RE.search(content)
    if match:
        return _parse_source_time(match.group(1))
    longmemeval = _LONGMEMEVAL_SOURCE_DATE_RE.search(content)
    if not longmemeval:
        return None
    try:
        parsed = datetime.strptime(
            f"{longmemeval.group(1)} {longmemeval.group(3)}", "%Y/%m/%d %H:%M"
        )
    except ValueError:
        return None
    if parsed.strftime("%a").lower() != longmemeval.group(2).lower():
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _temporal_order(items: List[Dict[str, Any]], intent: str) -> List[Dict[str, Any]]:
    if intent == "neutral":
        return items
    dated = [item for item in items if item["source_time"] is not None]
    undated = [item for item in items if item["source_time"] is None]
    ascending = sorted(dated, key=lambda item: (item["source_time"], item["hit_rank"]))
    if intent == "past":
        return ascending + undated
    if intent == "latest":
        newest_first = sorted(
            dated,
            key=lambda item: (item["source_time"], -item["hit_rank"]),
            reverse=True,
        )
        return newest_first + undated
    if len(ascending) < 2:
        return list(reversed(ascending)) + undated
    middle = sorted(
        ascending[1:-1],
        key=lambda item: (item["source_time"], -item["hit_rank"]),
        reverse=True,
    )
    return [ascending[-1], ascending[0], *middle, *undated]


def _numeric_fact_window(content: str, window_chars: int) -> Optional[tuple[int, int]]:
    """Return the first bounded source line containing a numeric fact."""
    cursor = 0
    for line in content.splitlines(keepends=True):
        end = cursor + len(line)
        if _NUMBER_RE.search(line):
            return cursor, min(end, cursor + window_chars)
        cursor = end
    return None


def _json_safe(value: Any) -> Any:
    """Return a deterministic JSON-safe projection of a supplied hit."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        # Avoid Python's decimal-string safety limit and unreasonable context
        # metadata while preserving ordinary retrieval IDs and scores.
        return value if value.bit_length() <= 1024 else _UNSAFE
    if isinstance(value, float):
        return value if math.isfinite(value) else _UNSAFE
    if isinstance(value, (list, tuple)):
        projected_items = []
        for item in value:
            projected = _json_safe(item)
            if projected is not _UNSAFE:
                projected_items.append(projected)
        return projected_items
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            projected = _json_safe(item)
            if projected is not _UNSAFE:
                safe[key] = projected
        return safe
    return _UNSAFE


_UNSAFE = object()


def _render(evidence: List[Dict[str, Any]]) -> str:
    """Use JSONL framing so source text cannot forge a citation boundary."""
    rendered = []
    for item in evidence:
        record = {"file": item["file"], "span": item["span"], "text": item["text"]}
        if item.get("source_time"):
            record["source_time"] = item["source_time"]
        rendered.append(json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ))
    return "\n".join(rendered)


class EvidenceContextMixin:
    """Compile broad search hits into compact, verbatim evidence windows."""

    def compile_evidence_context(
        self,
        query: str,
        *,
        results: Optional[List[Dict[str, Any]]] = None,
        top_k: int = 20,
        budget: int = 1000,
        window_chars: int = 400,
        adjacent_windows: int = 1,
        pinned_sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        query = _required_text(query, "query")
        _positive_int(top_k, "top_k")
        _positive_int(budget, "budget")
        _positive_int(window_chars, "window_chars")
        _nonnegative_int(adjacent_windows, "adjacent_windows")
        if results is not None and not isinstance(results, list):
            raise TypeError("results must be a list or None")
        if pinned_sources is not None and (
            not isinstance(pinned_sources, list)
            or any(not isinstance(item, str) or not item.strip() for item in pinned_sources)
        ):
            raise ValueError("pinned_sources must be a list of non-empty strings")

        raw_query_tokens = _TOKEN_RE.findall(query)
        if not raw_query_tokens:
            raise ValueError("query must contain a searchable token")
        query_tokens = _query_tokens(query)
        if query_tokens and window_chars < max(len(token) for token in query_tokens):
            raise ValueError("window_chars must fit the longest query token")

        supplied_results = results is not None
        temporal_intent = _temporal_intent(query)
        hits = self.search_v2(query, top_k=top_k) if results is None else list(results)
        indexed_hits = list(enumerate(hits))
        # Direct caller results are an audit boundary. Pins may prioritize only
        # automatically searched candidates, never the supplied hit order.
        if not supplied_results and pinned_sources:
            pinned = {item.strip() for item in pinned_sources}
            indexed_hits.sort(
                key=lambda pair: (
                    0 if isinstance(pair[1], dict) and pair[1].get("file") in pinned else 1,
                    pair[0],
                )
            )

        base = Path(self.base_dir).resolve()
        per_hit: List[Dict[str, Any]] = []
        for hit_rank, hit in indexed_hits:
            if not isinstance(hit, dict) or not isinstance(hit.get("file"), str):
                continue
            file_name = hit["file"]
            relative = Path(file_name)
            if relative.is_absolute():
                continue
            try:
                path = (base / relative).resolve()
                path.relative_to(base)
                content = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue
            windows = self._evidence_windows(content, query_tokens, window_chars, adjacent_windows)
            numeric_windows = []
            if not windows and _has_numeric_intent(query):
                numeric_window = _numeric_fact_window(content, window_chars)
                if numeric_window is not None:
                    numeric_windows.append(numeric_window)
            if not windows and not numeric_windows:
                continue
            score = hit.get("score", 0.0)
            try:
                numeric_score = float(score)
            except (OverflowError, TypeError, ValueError):
                numeric_score = 0.0
            if not math.isfinite(numeric_score):
                numeric_score = 0.0
            identity = _json_safe(hit)
            per_hit.append({
                "file": file_name,
                "hit_rank": hit_rank,
                "score": numeric_score,
                "hit": identity if isinstance(identity, dict) else {"file": file_name},
                "content": content,
                "windows": windows + numeric_windows,
                "anchored": bool(windows),
                "source_time": _source_time(hit, content),
            })

        # A source timestamp never makes a hit relevant by itself. Temporal
        # ordering applies to query anchors first and numeric auxiliaries last.
        if temporal_intent != "neutral":
            anchored = _temporal_order(
                [item for item in per_hit if item["anchored"]], temporal_intent
            )
            auxiliary = _temporal_order(
                [item for item in per_hit if not item["anchored"]], temporal_intent
            )
            per_hit = anchored + auxiliary

        selected: List[Dict[str, Any]] = []
        omitted: List[str] = []
        included_files = set()
        for window_index in range(max((len(item["windows"]) for item in per_hit), default=0)):
            for item in per_hit:
                if window_index >= len(item["windows"]):
                    continue
                start, end = item["windows"][window_index]
                evidence = {
                    "file": item["file"],
                    "span": [start, end],
                    "text": item["content"][start:end],
                    "score": item["score"],
                    "hit_rank": item["hit_rank"],
                    "hit": item["hit"],
                }
                if item["source_time"] is not None:
                    evidence["source_time"] = item["source_time"].isoformat()
                proposed = selected + [evidence]
                if estimate_tokens(_render(proposed)) > budget and "source_time" in evidence:
                    # Evidence text outranks optional timestamp metadata under a
                    # hard budget. Temporal ordering was already applied above.
                    evidence = dict(evidence)
                    evidence.pop("source_time")
                    proposed = selected + [evidence]
                if estimate_tokens(_render(proposed)) > budget:
                    # Reclaim optional metadata from prior records before
                    # dropping a new evidence body. Work on copies so a body
                    # that still cannot fit does not alter prior selections.
                    proposed = [dict(record) for record in proposed]
                    for record in reversed(proposed[:-1]):
                        record.pop("source_time", None)
                        if estimate_tokens(_render(proposed)) <= budget:
                            break
                if estimate_tokens(_render(proposed)) <= budget:
                    selected = proposed
                    included_files.add(item["file"])
                elif window_index == 0 and item["file"] not in included_files and item["file"] not in omitted:
                    omitted.append(item["file"])

        text = _render(selected)
        sources = []
        for item in selected:
            if item["file"] not in sources:
                sources.append(item["file"])
        identity = {
            "query": query,
            "budget": budget,
            "evidence": selected,
            "sources": sources,
            "omitted_hits": omitted,
        }
        usage_id = hashlib.sha256(json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")).hexdigest()
        return {
            "query": query,
            "budget": budget,
            "estimated_tokens": estimate_tokens(text),
            "text": text,
            "evidence": selected,
            "sources": sources,
            "omitted_hits": omitted,
            "usage_id": usage_id,
        }

    @staticmethod
    def _evidence_windows(
        content: str,
        query_tokens: List[str],
        window_chars: int,
        adjacent_windows: int,
    ) -> List[tuple]:
        lines = content.splitlines(keepends=True)
        offsets = []
        cursor = 0
        for line in lines:
            offsets.append(cursor)
            cursor += len(line)

        candidates = []
        token_set = set(query_tokens)
        for index, line in enumerate(lines):
            line_start = offsets[index]
            line_end = line_start + len(line)
            for match in _TOKEN_RE.finditer(line):
                if match.group(0).lower() not in token_set:
                    continue
                context_start = offsets[max(0, index - adjacent_windows)]
                context_end = offsets[min(len(lines), index + adjacent_windows + 1)] if index + adjacent_windows + 1 < len(lines) else len(content)
                if context_end - context_start <= window_chars:
                    candidates.append((context_start, context_end))
                    continue
                match_start = line_start + match.start()
                match_end = line_start + match.end()
                before = max(0, (window_chars - (match_end - match_start)) // 2)
                start = max(line_start, match_start - before)
                end = min(line_end, start + window_chars)
                start = max(line_start, end - window_chars)
                candidates.append((start, end))

        # Merge only when the merged source span remains inside the configured
        # per-item ceiling; otherwise keep separate evidence records.
        windows = []
        for start, end in sorted(set(candidates)):
            if windows and start <= windows[-1][1] and end - windows[-1][0] <= window_chars:
                windows[-1] = (windows[-1][0], max(windows[-1][1], end))
            else:
                windows.append((start, end))
        return windows
