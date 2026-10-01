"""Owner ledger: verbatim owner sayings and an irreversible-only decision queue.

Sayings are the owner's own words, stored exactly as said, so an agent can
check a past directive before asking the owner again.  The decision queue only
holds questions that genuinely need the owner (money, deletion, publishing,
accounts, ...); reversible work is told to proceed instead of waiting.

Storage is append-only JSONL via :func:`store_core.append`.  Reads skip
unparseable lines.  No LLM calls; search is deterministic token overlap.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .store_core import append

__all__ = ["OwnerLedgerMixin", "OwnerLedgerError", "IRREVERSIBLE_CATEGORIES"]

IRREVERSIBLE_CATEGORIES = frozenset({
    "money", "delete", "publish", "account", "credential", "legal", "external_contract",
})
_MAX_TEXT = 4000
_TOKEN = re.compile(r"[0-9A-Za-z\uac00-\ud7a3]+")


class OwnerLedgerError(ValueError):
    """Invalid owner-ledger input or unknown record id."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text)).split())


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:16]


_HANGUL = re.compile(r"[\uac00-\ud7a3]")
_STOP = frozenset({"하는", "한테", "에게", "해도", "하고", "해줘", "the", "and", "for"})


def _jamo_initial(ch: str) -> str:
    """Initial consonant of a Hangul syllable (시/시/스 -> ㅅ), for stem matching."""
    code = ord(ch) - 0xAC00
    return chr(0x1100 + code // 588) if 0 <= code < 11172 else ch


def _tokens(text: str) -> set:
    """Whole tokens plus Korean stem keys.

    Korean conjugates the verb ending (시키지마 / 시켜도 / 시킨다), so whole-token
    overlap misses obvious matches.  Each Hangul token also contributes its first
    syllable followed by the initial consonant of the second (시키 -> 시ᄏ,
    시켜 -> 시ᄏ), which is stable across these endings without an analyzer.
    """
    out = set()
    for tok in _TOKEN.findall(_norm(text).lower()):
        if tok in _STOP:
            continue
        out.add(tok)
        if _HANGUL.match(tok) and len(tok) >= 2:
            out.add("k:" + tok[0] + _jamo_initial(tok[1]))
    return out


def _clean(field: str, value: Any, limit: int = _MAX_TEXT) -> str:
    text = _norm(value or "")
    if not text:
        raise OwnerLedgerError("{} is required".format(field))
    if len(text) > limit:
        raise OwnerLedgerError("{} exceeds {} chars".format(field, limit))
    return text


def _read(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


class OwnerLedgerMixin:
    """Mixed into :class:`MemKraft`.  ``owner_dir`` may be overridden per store."""

    @property
    def owner_dir(self) -> Path:
        override = getattr(self, "_owner_dir_override", None)
        if override:
            return Path(override)
        return Path(getattr(self, "base_dir", ".")) / "owner"

    @owner_dir.setter
    def owner_dir(self, value: Any) -> None:
        self._owner_dir_override = str(value) if value else None

    # -- sayings -----------------------------------------------------------
    def _sayings(self) -> Dict[str, Dict[str, Any]]:
        live: Dict[str, Dict[str, Any]] = {}
        for row in _read(self.owner_dir / "sayings.jsonl"):
            sid = row.get("saying_id")
            if row.get("kind") == "saying" and sid and sid not in live:
                live[sid] = row
            elif row.get("kind") == "saying_retired" and sid in live:
                live[sid] = dict(live[sid], retired=True, retired_reason=row.get("reason", ""))
        return live

    def saying_record(self, text: str, *, scope: str = "all", tags: Iterable[str] = (),
                      source: str = "", said_at: Optional[str] = None) -> Dict[str, Any]:
        body = _clean("text", text)
        scope = _clean("scope", scope, 64).lower()
        sid = "say-" + _digest(body, scope)
        existing = self._sayings().get(sid)
        if existing and not existing.get("retired"):
            return {"id": sid, "created": False}
        append(self.owner_dir / "sayings.jsonl", {
            "kind": "saying", "saying_id": sid, "text": body, "scope": scope,
            "tags": sorted({_norm(t).lower() for t in tags if _norm(t)}),
            "source": _norm(source)[:256], "said_at": said_at or _now(),
        })
        return {"id": sid, "created": True}

    def saying_retire(self, saying_id: str, reason: str) -> Dict[str, Any]:
        if saying_id not in self._sayings():
            raise OwnerLedgerError("unknown saying id")
        append(self.owner_dir / "sayings.jsonl", {
            "kind": "saying_retired", "saying_id": saying_id,
            "reason": _clean("reason", reason, 500),
        })
        return {"id": saying_id, "retired": True}

    def saying_search(self, query: str, *, scope: Optional[str] = None,
                      limit: int = 5) -> List[Dict[str, Any]]:
        q = _tokens(query)
        if not q:
            return []
        want = (scope or "").strip().lower()
        scored = []
        for row in self._sayings().values():
            if row.get("retired"):
                continue
            if want and row.get("scope") not in (want, "all"):
                continue
            tok = _tokens(row.get("text", "")) | set(row.get("tags") or [])
            overlap = len(q & tok)
            if overlap:
                scored.append((overlap / (len(q) ** 0.5 * max(len(tok), 1) ** 0.5), row))
        scored.sort(key=lambda item: (-item[0], item[1].get("said_at", "")))
        return [{"id": r["saying_id"], "text": r["text"], "scope": r["scope"],
                 "tags": r.get("tags", []), "source": r.get("source", ""),
                 "said_at": r.get("said_at", ""), "score": round(s, 3)}
                for s, r in scored[:max(1, int(limit))]]

    # -- decision queue ----------------------------------------------------
    def _decisions(self) -> Dict[str, Dict[str, Any]]:
        state: Dict[str, Dict[str, Any]] = {}
        for row in _read(self.owner_dir / "decisions.jsonl"):
            did = row.get("decision_id")
            if row.get("kind") == "decision_open" and did and did not in state:
                state[did] = dict(row, status="open")
            elif row.get("kind") == "decision_resolved" and did in state:
                state[did] = dict(state[did], status="resolved", answer=row.get("answer", ""),
                                  resolved_by=row.get("resolved_by", ""),
                                  resolved_at=row.get("resolved_at", ""))
        return state

    def decision_enqueue(self, question: str, *, category: str, requester: str,
                         options: Iterable[str] = (), context: str = "",
                         reversible: bool = False) -> Dict[str, Any]:
        body = _clean("question", question, 1000)
        category = _clean("category", category, 64).lower()
        requester = _clean("requester", requester, 64).lower()
        if reversible and category not in IRREVERSIBLE_CATEGORIES:
            return {"queued": False, "action": "proceed"}
        did = "dec-" + _digest(body, requester)
        current = self._decisions().get(did)
        if current and current["status"] == "open":
            return {"queued": True, "id": did, "created": False}
        append(self.owner_dir / "decisions.jsonl", {
            "kind": "decision_open", "decision_id": did, "question": body,
            "category": category, "requester": requester,
            "options": [_norm(o) for o in options if _norm(o)][:8],
            "context": _norm(context)[:1000], "opened_at": _now(),
        })
        return {"queued": True, "id": did, "created": True}

    def decision_resolve(self, decision_id: str, answer: str, *, resolved_by: str) -> Dict[str, Any]:
        current = self._decisions().get(decision_id)
        if not current:
            raise OwnerLedgerError("unknown decision id")
        if current["status"] != "open":
            return {"id": decision_id, "status": current["status"], "answer": current.get("answer", "")}
        append(self.owner_dir / "decisions.jsonl", {
            "kind": "decision_resolved", "decision_id": decision_id,
            "answer": _clean("answer", answer, 1000),
            "resolved_by": _clean("resolved_by", resolved_by, 64).lower(),
            "resolved_at": _now(),
        })
        return {"id": decision_id, "status": "resolved"}

    def decision_list(self, status: str = "open") -> List[Dict[str, Any]]:
        rows = [r for r in self._decisions().values() if r["status"] == status]
        rows.sort(key=lambda r: r.get("opened_at", ""))
        keep = ("decision_id", "question", "category", "requester", "options", "context",
                "opened_at", "status", "answer", "resolved_by", "resolved_at")
        return [{k: r[k] for k in keep if k in r} for r in rows]

    def decision_digest(self) -> str:
        open_items = self.decision_list()
        if not open_items:
            return ""
        by_req: Dict[str, List[Dict[str, Any]]] = {}
        for item in open_items:
            by_req.setdefault(item["requester"], []).append(item)
        lines = ["Open decisions ({}):".format(len(open_items))]
        for req in sorted(by_req):
            lines.append("- {}:".format(req))
            for item in by_req[req]:
                opts = " / ".join(item.get("options") or [])
                lines.append("  - [{}] {}{}".format(
                    item["category"], item["question"], " ({})".format(opts) if opts else ""))
        return "\n".join(lines)
