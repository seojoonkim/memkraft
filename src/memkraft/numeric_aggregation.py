"""Conservative numeric aggregation over explicit, provenance-bearing evidence."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_SUM_RE = re.compile(r"(?:\b(?:total|sum|altogether)\b|합계|총합)", re.IGNORECASE)
_COUNT_RE = re.compile(r"(?:\b(?:count|number\s+of|how\s+many\s+times)\b|개수|몇\s*번|횟수)", re.IGNORECASE)
_DURATION_RE = re.compile(r"(?:\b(?:duration|how\s+long|period\s+between)\b|기간|얼마나\s*오래|며칠)", re.IGNORECASE)
_UNSUPPORTED_RE = re.compile(
    r"(?:\b(?:average|mean|ratio|rate|convert|conversion|per\s+unit|unit\s+price\s+times|times\s+quantity)\b|평균|비율|환산|변환|단가)",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)
_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_VERSION_RE = re.compile(r"(?i)\b(?:v(?:ersion)?\s*\d+(?:\.\d+){1,3}|\d+(?:\.\d+){2,3})\b")
_ID_NUMBER_RE = re.compile(r"(?i)\b(?:id|build|version|release)\s*(?:[:#=-]?\s*)\d[\d.-]*")

# Deliberately finite: unknown units are not guessed or converted.
_CURRENCY_PREFIX = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "KRW", "CAD", "AUD", "CNY"}
_UNITS = {
    "kg": "kg", "g": "g", "lb": "lb", "lbs": "lb", "oz": "oz",
    "km": "km", "m": "m", "cm": "cm", "mi": "mi",
    "l": "L", "ml": "mL", "hours": "hours", "hour": "hours",
    "minutes": "minutes", "minute": "minutes", "days": "days", "day": "days",
}
_UNIT_SUFFIXES = sorted(_UNITS, key=lambda unit: (-len(unit), unit))
_SUFFIX_PATTERN = "|".join(re.escape(unit) for unit in _UNIT_SUFFIXES)
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_QUANTITY_RE = re.compile(
    rf"(?P<leading_sign>[+-])?\s*"
    rf"(?:(?P<symbol>[$€£¥])\s*|(?P<prefix>[A-Za-z]{{3}})\s+)?"
    rf"(?P<trailing_sign>[+-])?\s*(?P<number>{_NUMBER})"
    rf"(?:\s*(?P<suffix>USD|EUR|GBP|JPY|KRW|CAD|AUD|CNY|{_SUFFIX_PATTERN})"
    rf"(?![A-Za-z]))?",
    re.IGNORECASE,
)

_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "between", "by", "did", "do", "does",
    "for", "from", "how", "in", "is", "it", "of", "on", "or", "the", "to", "was",
    "were", "what", "when", "with", "total", "sum", "altogether", "count", "number",
    "many", "times", "duration", "long", "period", "cost", "price", "amount", "value",
    "합계", "총합", "개수", "몇", "번", "횟수", "기간", "얼마나", "오래", "며칠",
}


def _result(operation: Optional[str], status: str, *, value: Optional[Decimal] = None,
            unit: Optional[str] = None, facts: Optional[List[Dict[str, Any]]] = None,
            excluded: Optional[List[Dict[str, Any]]] = None, reason: Optional[str] = None,
            scope: str = "retrieved_hits_only") -> Dict[str, Any]:
    return {
        "operation": operation,
        "status": status,
        "value": value,
        "unit": unit,
        "facts": facts or [],
        "excluded": excluded or [],
        "reason": reason,
        "scope": scope,
    }


def _operation(query: str) -> Optional[str]:
    if _UNSUPPORTED_RE.search(query):
        return None
    matches = [
        name for name, pattern in (("duration", _DURATION_RE), ("count", _COUNT_RE), ("sum", _SUM_RE))
        if pattern.search(query)
    ]
    return matches[0] if len(matches) == 1 else None


def _anchors(query: str) -> List[str]:
    return [token.lower() for token in _TOKEN_RE.findall(query) if token.lower() not in _STOPWORDS]


def _root(token: str) -> str:
    """Tiny deterministic inflection normalizer, not semantic expansion."""
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token


def _relevant(line: str, anchors: List[str]) -> bool:
    if not anchors:
        return True
    line_tokens = {_root(token.lower()) for token in _TOKEN_RE.findall(line)}
    return any(_root(anchor) in line_tokens for anchor in anchors)


def _read_hits(owner: Any, query: str, results: Optional[List[Dict[str, Any]]], top_k: int
               ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str], str]:
    supplied = results is not None
    hits = list(results) if supplied else list(owner.search_v2(query, top_k=top_k))
    base = Path(owner.base_dir).resolve()
    loaded: List[Dict[str, Any]] = []
    for rank, hit in enumerate(hits):
        if not isinstance(hit, dict) or not isinstance(hit.get("file"), str) or not hit["file"]:
            return None, "a hit has no readable relative file", "supplied_hits_only" if supplied else "retrieved_hits_only"
        relative = Path(hit["file"])
        if relative.is_absolute():
            return None, f"path escapes base_dir: {hit['file']}", "supplied_hits_only" if supplied else "retrieved_hits_only"
        try:
            path = (base / relative).resolve()
            path.relative_to(base)
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            return None, f"missing, unreadable, or escaping source: {hit['file']}", "supplied_hits_only" if supplied else "retrieved_hits_only"
        loaded.append({"file": hit["file"], "rank": rank, "hit": hit, "content": content})
    return loaded, None, "supplied_hits_only" if supplied else "retrieved_hits_only"


def _lines(item: Dict[str, Any]):
    cursor = 0
    for line in item["content"].splitlines(keepends=True):
        end = cursor + len(line)
        yield cursor, end, line
        cursor = end


def _excluded(item: Dict[str, Any], start: int, end: int, text: str, reason: str) -> Dict[str, Any]:
    return {"file": item["file"], "span": [start, end], "text": text, "reason": reason}


def _fact(item: Dict[str, Any], start: int, end: int, text: str, value: Optional[Decimal],
          unit: Optional[str], *, date_value: Optional[str] = None) -> Dict[str, Any]:
    fact_id = item["hit"].get("fact_id")
    return {
        "file": item["file"], "span": [start, end], "text": text,
        "value": value, "unit": unit,
        "date": date_value if date_value is not None else (
            item["hit"].get("valid_from") if isinstance(item["hit"].get("valid_from"), str) else None
        ),
        "fact_id": fact_id if isinstance(fact_id, (str, int)) else None,
        "hit_rank": item["rank"],
    }


def _fact_signature(fact: Dict[str, Any]) -> tuple:
    return (
        str(fact["value"].normalize()) if fact["value"] is not None else None,
        fact["unit"], fact.get("date"), " ".join(fact["text"].casefold().split()),
    )


def _deduplicate(facts: List[Dict[str, Any]], *, conflicting_ids: bool = True,
                 distinguish_id_spans: bool = False
                 ) -> Tuple[List[Dict[str, Any]], bool]:
    seen_ids: Dict[tuple, tuple] = {}
    seen_unprovenanced: Dict[str, str] = {}
    kept = []
    ambiguous = False
    for fact in facts:
        fact_id = fact["fact_id"]
        if fact_id is not None:
            key = (type(fact_id).__name__, str(fact_id))
            signature = _fact_signature(fact) + (
                (fact["file"], tuple(fact["span"])) if distinguish_id_spans else ()
            )
            previous = seen_ids.get(key)
            if previous is not None:
                if previous == signature:
                    continue
                if conflicting_ids:
                    ambiguous = True
            else:
                seen_ids[key] = signature
        else:
            # Canonicalize notation as well as whitespace: ``$3`` and
            # ``USD 3`` are the same unprovenanced claim, not two expenses.
            semantic = re.sub(
                r"[$€£¥]|\b(?:USD|EUR|GBP|JPY|KRW|CAD|AUD|CNY)\b|\d[\d,.-]*",
                " ", fact["text"], flags=re.IGNORECASE,
            )
            canonical = repr((
                " ".join(_TOKEN_RE.findall(semantic.casefold())),
                str(fact["value"].normalize()) if fact["value"] is not None else None,
                fact["unit"],
            ))
            previous_file = seen_unprovenanced.get(canonical)
            if previous_file is not None and previous_file != fact["file"]:
                ambiguous = True
            else:
                seen_unprovenanced[canonical] = fact["file"]
        kept.append(fact)
    return kept, ambiguous


def _quantities(line: str) -> List[Tuple[Decimal, Optional[str]]]:
    blocked = [match.span() for regex in (_ISO_DATE_RE, _VERSION_RE, _ID_NUMBER_RE) for match in regex.finditer(line)]
    found = []
    for match in _QUANTITY_RE.finditer(line):
        if any(match.start() < end and match.end() > start for start, end in blocked):
            continue
        symbol, prefix, suffix = match.group("symbol", "prefix", "suffix")
        unit = None
        if symbol:
            unit = _CURRENCY_PREFIX[symbol]
        elif prefix and prefix.upper() in _CURRENCIES:
            unit = prefix.upper()
        elif suffix:
            upper = suffix.upper()
            unit = upper if upper in _CURRENCIES else _UNITS.get(suffix.lower())
        leading_sign, trailing_sign = match.group("leading_sign", "trailing_sign")
        if leading_sign and trailing_sign:
            continue
        try:
            value = Decimal((leading_sign or trailing_sign or "") + match.group("number").replace(",", ""))
        except InvalidOperation:
            continue
        found.append((value, unit))
    return found


_NON_EVENT_ROW_RE = re.compile(r"(?i)^\s*(?:[-*]\s*)?[^:]{0,80}\b(?:status|state)\s*:")


def _explicit_event(line: str) -> bool:
    """Accept concrete occurrence records, never entity labels/state rows."""
    return not _NON_EVENT_ROW_RE.search(line) and bool(_ISO_DATE_RE.search(line))


def _event_predicates(query: str) -> List[str]:
    """Extract explicit event verbs; do not infer an event from entity terms."""
    tokens = [token.lower() for token in _TOKEN_RE.findall(query)]
    predicates = []
    for index, token in enumerate(tokens):
        follows_auxiliary = index > 0 and tokens[index - 1] in {
            "be", "been", "being", "is", "was", "were",
        }
        if token not in _STOPWORDS and (token.endswith(("ed", "ing")) or follows_auxiliary):
            predicates.append(token)
    return predicates


def _contains_predicates(line: str, predicates: List[str]) -> bool:
    line_tokens = {_root(token.lower()) for token in _TOKEN_RE.findall(line)}
    return bool(predicates) and all(_root(predicate) in line_tokens for predicate in predicates)


class NumericAggregationMixin:
    """Add a minimal, fail-closed numeric evidence aggregation API."""

    def aggregate_numeric_evidence(
        self, query: str, results: Optional[List[Dict[str, Any]]] = None, top_k: int = 50
    ) -> Dict[str, Any]:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if results is not None and not isinstance(results, list):
            raise TypeError("results must be a list or None")
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be a positive integer")

        operation = _operation(query)
        if operation is None:
            return _result(None, "unsupported", reason="no single explicitly supported operation")

        loaded, error, scope = _read_hits(self, query, results, top_k)
        if error is not None:
            return _result(operation, "incomplete", reason=error, scope=scope)
        assert loaded is not None
        anchors = _anchors(query)
        excluded: List[Dict[str, Any]] = []

        if operation == "duration":
            endpoints: List[Tuple[date, Dict[str, Any]]] = []
            for item in loaded:
                for start, end, line in _lines(item):
                    if not _relevant(line, anchors):
                        if line.strip():
                            excluded.append(_excluded(item, start, end, line, "unrelated"))
                        continue
                    for match in _ISO_DATE_RE.finditer(line):
                        try:
                            endpoint = date.fromisoformat(match.group(1))
                        except ValueError:
                            return _result(operation, "incomplete", excluded=excluded,
                                           reason="invalid ISO date endpoint", scope=scope)
                        match_start, match_end = start + match.start(), start + match.end()
                        endpoints.append((endpoint, _fact(
                            item, match_start, match_end, match.group(1), None, "days",
                            date_value=match.group(1),
                        )))
            if len(endpoints) > 2:
                return _result(operation, "ambiguous", facts=[entry[1] for entry in endpoints],
                               excluded=excluded, reason="duration requires exactly two ISO date endpoints", scope=scope)
            if len(endpoints) < 2:
                return _result(operation, "incomplete", facts=[entry[1] for entry in endpoints],
                               excluded=excluded, reason="duration requires exactly two ISO date endpoints", scope=scope)
            # Endpoints are distinct subfacts even when their enclosing hit has
            # one hit-level fact_id; exact endpoint spans provide provenance.
            facts, duplicate = _deduplicate(
                [entry[1] for entry in endpoints], conflicting_ids=False,
                distinguish_id_spans=True,
            )
            if duplicate:
                return _result(operation, "ambiguous", facts=facts, excluded=excluded,
                               reason="identical cross-file fact lacks fact_id provenance", scope=scope)
            elapsed = abs((endpoints[1][0] - endpoints[0][0]).days)
            return _result(operation, "ok", value=Decimal(elapsed), unit="days", facts=facts,
                           excluded=excluded, scope=scope)

        if operation == "count":
            predicates = _event_predicates(query)
            if not predicates:
                return _result(operation, "ambiguous", excluded=excluded,
                               reason="count requires an explicit event predicate", scope=scope)
            candidates = []
            rejected_relevant = False
            for item in loaded:
                for start, end, line in _lines(item):
                    if (line.strip() and _relevant(line, anchors) and _explicit_event(line)
                            and _contains_predicates(line, predicates)):
                        candidates.append(_fact(item, start, end, line, Decimal(1), "events"))
                    elif line.strip() and _relevant(line, anchors):
                        rejected_relevant = True
                        excluded.append(_excluded(item, start, end, line, "not an explicit event occurrence"))
                    elif line.strip():
                        excluded.append(_excluded(item, start, end, line, "unrelated"))
            facts, duplicate = _deduplicate(candidates, conflicting_ids=False)
            if duplicate:
                return _result(operation, "ambiguous", facts=facts, excluded=excluded,
                               reason="identical cross-file event lacks fact_id provenance", scope=scope)
            if not facts:
                return _result(operation, "ambiguous" if rejected_relevant else "incomplete",
                               excluded=excluded, reason="no explicit event occurrences in matching rows",
                               scope=scope)
            return _result(operation, "ok", value=Decimal(len(facts)), unit="events", facts=facts,
                           excluded=excluded, scope=scope)

        candidates = []
        for item in loaded:
            for start, end, line in _lines(item):
                if not line.strip():
                    continue
                if not _relevant(line, anchors):
                    excluded.append(_excluded(item, start, end, line, "unrelated"))
                    continue
                quantities = _quantities(line)
                if len(quantities) > 1:
                    return _result(operation, "ambiguous", facts=candidates, excluded=excluded,
                                   reason="a relevant line contains multiple quantities", scope=scope)
                if not quantities:
                    excluded.append(_excluded(item, start, end, line, "no aggregatable quantity"))
                    continue
                value, unit = quantities[0]
                candidates.append(_fact(item, start, end, line, value, unit))

        facts, duplicate = _deduplicate(candidates)
        if duplicate:
            return _result(operation, "ambiguous", facts=facts, excluded=excluded,
                           reason="identical cross-file fact lacks fact_id provenance", scope=scope)
        if not facts:
            return _result(operation, "incomplete", excluded=excluded,
                           reason="no matching aggregatable quantities in retrieved hits", scope=scope)
        units = {fact["unit"] for fact in facts}
        if len(units) != 1:
            return _result(operation, "ambiguous", facts=facts, excluded=excluded,
                           reason="mixed units, currencies, or unitful/unitless quantities", scope=scope)
        unit = next(iter(units))
        return _result(operation, "ok", value=sum((fact["value"] for fact in facts), Decimal(0)),
                       unit=unit, facts=facts, excluded=excluded, scope=scope)
