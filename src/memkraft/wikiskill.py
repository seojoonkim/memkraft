"""Explicit, deterministic WikiSkill-style records; no extraction or execution.

Verification is a caller attestation, not a claim that this module ran a test.
All writes use the shared locked JSONL append primitive.
"""
from __future__ import annotations

from copy import deepcopy

from .store_core import append, read_all, RecordNotFoundError


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + " must be a non-empty string")
    return value


def _verification(value):
    if not isinstance(value, dict) or value.get("status") not in (
        "unverified", "verified", "failed"
    ):
        raise ValueError("verification requires status unverified, verified, or failed")
    if value["status"] == "verified":
        _text(value.get("verified_by"), "verified_by")
        evidence = value.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("verified experience requires evidence")
        for item in evidence:
            _text(item, "evidence item")
    return deepcopy(value)


def _verified_record(path, record_id, kind):
    _text(record_id, "record_id")
    records = [r for r in read_all(path).records if r.get("id") == record_id]
    if not records:
        raise RecordNotFoundError(path, record_id)
    if len(records) != 1 or records[0].get("kind") != kind:
        raise ValueError("source must identify exactly one " + kind)
    record = records[0]
    checked = _verification(record.get("verification"))
    if checked["status"] != "verified":
        raise ValueError("source must be verified")
    _text(record.get("text"), "source text")
    _text(record.get("provenance_id"), "source provenance_id")
    return record


class WikiSkillMixin:
    """Additive APIs isolated from candidate memory and automatic recall."""

    def skill_list_verified(self, *, name=None):
        """Return only persisted reusable skills whose sources remain verified."""
        records = read_all(self.base_dir / ".memkraft" / "skills.jsonl").records
        out = []
        for record in records:
            if record.get("kind") != "reusable_skill" or (name is not None and record.get("name") != name):
                continue
            if not record.get("steps") or not record.get("wiki_ids"):
                continue
            try:
                for wiki_id in record["wiki_ids"]:
                    _verified_record(self.base_dir / ".memkraft" / "wiki.jsonl", wiki_id, "wiki_knowledge")
            except (KeyError, ValueError, RecordNotFoundError):
                continue
            out.append(deepcopy(record))
        return out

    def skill_compile(self, name, *, wiki_ids):
        """Append an inert skill record from verified wiki text in requested order.

        Duplicate IDs are removed in first-seen order. No new procedural text
        is invented; each step is an exact stored wiki text. Repeated calls
        append separate records (only IDs/timestamps are non-deterministic).
        """
        _text(name, "name")
        if not isinstance(wiki_ids, (list, tuple)) or not wiki_ids:
            raise ValueError("wiki_ids must be a non-empty list or tuple")
        for wiki_id in wiki_ids:
            _text(wiki_id, "wiki_id")
        ids = list(dict.fromkeys(wiki_ids))
        root = self.base_dir / ".memkraft"
        sources = [_verified_record(root / "wiki.jsonl", key, "wiki_knowledge") for key in ids]
        return append(root / "skills.jsonl", {
            "kind": "reusable_skill", "name": name, "wiki_ids": ids,
            "steps": [r["text"] for r in sources],
            "experience_ids": [r["experience_id"] for r in sources],
            "provenance_ids": [r["provenance_id"] for r in sources],
            "source_verifications": [r["verification"] for r in sources],
        })

    def experience_promote(self, experience_id, *, title):
        """Copy a stored verified experience into wiki knowledge, without rewriting it."""
        _text(title, "title")
        root = self.base_dir / ".memkraft"
        source = _verified_record(root / "experiences.jsonl", experience_id, "raw_experience")
        return append(root / "wiki.jsonl", {
            "kind": "wiki_knowledge", "title": title, "text": source["text"],
            "experience_id": source["id"], "provenance_id": source["provenance_id"],
            "verification": source["verification"],
        })

    def experience_append(self, text, *, provenance_id, session_id=None, verification=None):
        """Append exact raw text and caller-supplied provenance/verification.

        ``verification`` defaults to ``{"status": "unverified"}``. Verified
        records require non-empty ``verified_by`` and a list of evidence refs.
        """
        _text(text, "text")
        _text(provenance_id, "provenance_id")
        if session_id is not None:
            _text(session_id, "session_id")
        checked = _verification({"status": "unverified"} if verification is None else verification)
        return append(self.base_dir / ".memkraft" / "experiences.jsonl", {
            "kind": "raw_experience", "text": text, "provenance_id": provenance_id,
            "session_id": session_id, "verification": checked,
        })
