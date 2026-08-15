"""Normative NFC, presence-and-length framed SHA-256 identities."""
import hashlib
import json
import struct
import unicodedata
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def frame(value: Any) -> bytes:
    if value is None:
        return b"\x00" + struct.pack(">Q", 0)
    if isinstance(value, (list, dict, tuple)):
        value = canonical_json(value)
    elif not isinstance(value, str):
        value = str(value)
    raw = unicodedata.normalize("NFC", value).encode("utf-8")
    return b"\x01" + struct.pack(">Q", len(raw)) + raw


def digest_fields(*values: Any) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(frame(value))
    return "sha256:" + digest.hexdigest()
