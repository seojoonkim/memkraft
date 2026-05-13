"""StructMem — MemKraft v2.9.2.

Regex+pattern based structured extraction. No LLM dependency.

Pulls dates, money amounts, URLs, emails, percentages, version strings
out of free text and (optionally) routes them to ``fact_add`` for a
given entity.

Design principles
-----------------
* Additive only — new mixin, never touches existing APIs.
* Stdlib only.
* Tolerant — bad input never raises; returns empty results.
* Idempotent extraction — same text in → same dict out (no LLM jitter).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


__all__ = ["StructMemMixin"]


# ── Patterns ────────────────────────────────────────────────────────
# ISO dates and YYYY/MM/DD (most common in Asian + technical contexts).
_DATE_RE = re.compile(
    r"\b("
    r"\d{4}-\d{2}-\d{2}"           # 2026-05-14
    r"|\d{4}/\d{1,2}/\d{1,2}"      # 2026/5/14
    r"|\d{4}\.\d{1,2}\.\d{1,2}"    # 2026.05.14
    r")\b"
)

# URLs — http(s) only, must contain a dot, no whitespace.
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

# Emails — RFC-5322-lite (good enough for extraction).
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Money — symbol-prefixed amounts (USD/KRW/EUR/JPY/GBP common).
# Examples: $12.99, $1,000, ₩50,000, ₩50000, €99, £1,200.50, ¥1500
_MONEY_RE = re.compile(
    r"(?P<symbol>[\$₩€£¥])\s*"
    r"(?P<amount>\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
)

# Percentages — 12%, 12.5%, 100%
_PERCENT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*%")

# Version strings — v1.2.3, 2.9.1, v0.9.2-alpha
_VERSION_RE = re.compile(
    r"\bv?(\d+\.\d+(?:\.\d+)?(?:[-+][\w.]+)?)\b"
)

# Phone numbers — international and KR-style.
# Conservative: requires 7+ digits with separators or 10+ raw digits.
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)\d{3,4}[-.\s]?\d{4}"
)


def _dedup_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ── Mixin ───────────────────────────────────────────────────────────
class StructMemMixin:
    """Adds ``extract_structured`` to ``MemKraft``."""

    def extract_structured(
        self,
        text: str,
        entity_hint: Optional[str] = None,
        auto_save: bool = False,
    ) -> Dict[str, Any]:
        """Extract structured fields from free text.

        Parameters
        ----------
        text:
            Source text. Empty/None → empty result.
        entity_hint:
            Optional entity name. When ``auto_save=True``, extracted
            fields are written to this entity via ``fact_add``.
            (No-op when ``auto_save=False`` or hint is empty.)
        auto_save:
            When True and ``entity_hint`` is given, calls ``fact_add``
            for each extracted field. The first occurrence of each
            type becomes the canonical value (key = field name).
            Multiple URLs etc. get suffixed keys (``url``, ``url_2``,
            ``url_3``…). Failures are silently swallowed — extraction
            still succeeds.

        Returns
        -------
        ``{
            "dates": [str, ...],         # ISO-ish date strings
            "urls": [str, ...],
            "emails": [str, ...],
            "money": [{"symbol": str, "amount": str, "raw": str}, ...],
            "percentages": [str, ...],   # "12.5" (no % sign)
            "versions": [str, ...],      # "2.9.1"
            "phones": [str, ...],
            "saved": int,                # number of fact_add calls (0 unless auto_save)
        }``
        """
        result: Dict[str, Any] = {
            "dates": [],
            "urls": [],
            "emails": [],
            "money": [],
            "percentages": [],
            "versions": [],
            "phones": [],
            "saved": 0,
        }
        if not text or not isinstance(text, str):
            return result

        # Dates
        result["dates"] = _dedup_preserve_order(_DATE_RE.findall(text))
        # URLs
        result["urls"] = _dedup_preserve_order(
            [u.rstrip(".,);]") for u in _URL_RE.findall(text)]
        )
        # Emails
        result["emails"] = _dedup_preserve_order(_EMAIL_RE.findall(text))
        # Money — keep raw + parsed
        seen_money = set()
        for m in _MONEY_RE.finditer(text):
            raw = m.group(0)
            if raw in seen_money:
                continue
            seen_money.add(raw)
            result["money"].append({
                "symbol": m.group("symbol"),
                "amount": m.group("amount"),
                "raw": raw,
            })
        # Percentages
        result["percentages"] = _dedup_preserve_order(_PERCENT_RE.findall(text))
        # Versions — must NOT also match dates or money amounts already pulled.
        date_set = set(result["dates"])
        money_amount_set = {m["amount"] for m in result["money"]}
        versions: List[str] = []
        for v in _VERSION_RE.findall(text):
            # Skip things that look like dates (4-digit start with dots).
            if v in date_set:
                continue
            # Skip values that are actually money amounts (e.g. 12.99 in $12.99).
            if v in money_amount_set:
                continue
            # Skip plain integers (regex re-anchors caught "2.9" as version,
            # but a bare "2026" wouldn't — guard anyway).
            if "." not in v:
                continue
            versions.append(v)
        result["versions"] = _dedup_preserve_order(versions)
        # Phones — apply only when no email match overlaps the same span.
        # Simpler: extract all, then drop any phone that's a substring of an email.
        raw_phones = _PHONE_RE.findall(text)
        email_blob = " ".join(result["emails"])
        result["phones"] = _dedup_preserve_order(
            [p.strip() for p in raw_phones if p.strip() and p not in email_blob]
        )

        # Optional auto-save via fact_add
        if auto_save and entity_hint and hasattr(self, "fact_add"):
            entity = str(entity_hint).strip()
            if entity:
                saved = 0

                def _save(key: str, value: str) -> None:
                    nonlocal saved
                    try:
                        self.fact_add(entity, key, value)
                        saved += 1
                    except Exception:
                        # Best-effort only — never let fact_add break extraction.
                        pass

                # First-occurrence canonical keys; extras get _2, _3 suffix.
                def _save_list(key_base: str, values: List[str]) -> None:
                    for i, v in enumerate(values):
                        key = key_base if i == 0 else f"{key_base}_{i + 1}"
                        _save(key, v)

                _save_list("date", result["dates"])
                _save_list("url", result["urls"])
                _save_list("email", result["emails"])
                _save_list("percent", result["percentages"])
                _save_list("version", result["versions"])
                _save_list("phone", result["phones"])
                for i, m in enumerate(result["money"]):
                    key = "money" if i == 0 else f"money_{i + 1}"
                    _save(key, m["raw"])

                result["saved"] = saved

        return result
