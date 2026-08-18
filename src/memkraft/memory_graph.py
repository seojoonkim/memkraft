"""Strict append-only, bitemporal exact memory graph."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None
from pathlib import Path
from typing import Any, Dict, List, Optional

from .correction_policy import _read as _read_correction_ledger

RECORD_TYPES = frozenset({"claim", "relation", "lifecycle"})
RELATION_TYPES = frozenset({"supports", "contradicts", "supersedes", "derived_from"})
LIFECYCLE_STATES = frozenset({"active", "superseded", "contradicted", "retired"})
_ID_PATTERNS = {
    "claim_id": re.compile(r"^mgc-[0-9a-f]{16}$"),
    "relation_id": re.compile(r"^mgr-[0-9a-f]{16}$"),
    "record_id": re.compile(r"^mg-[0-9a-f]{16}$"),
}
_COMMON = {"schema_version", "event_seq", "record_id", "record_type", "operation_id",
           "batch_index", "batch_size", "payload_hash", "tx_time"}
_FIELDS = {
    "claim": {"claim_id", "canonical_key", "statement", "scope", "artifact_refs",
              "provenance", "valid_from", "valid_to"},
    "relation": {"relation_id", "relation_type", "src_claim_id", "dst_claim_id",
                 "evidence_artifact_refs", "correction_event_refs", "valid_from", "valid_to"},
    "lifecycle": {"claim_id", "state", "caused_by", "reason", "evidence_refs",
                  "valid_from", "valid_to"},
}


class MemoryGraphError(Exception): pass
class MemoryGraphValidationError(MemoryGraphError): pass
class MemoryGraphLimitError(MemoryGraphValidationError): pass
class MemoryGraphCASError(MemoryGraphError): pass
class MemoryGraphDuplicateOperationError(MemoryGraphError): pass
class MemoryGraphCorruptError(MemoryGraphError): pass
class MemoryGraphLockTimeoutError(MemoryGraphError): pass


def _canonical(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise MemoryGraphValidationError("canonical_key must be non-empty text")
    return " ".join(unicodedata.normalize("NFC", text).lower().split())


def _instant(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise MemoryGraphValidationError(field + " must be an ISO-8601 instant")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MemoryGraphValidationError(field + " must be an ISO-8601 instant")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MemoryGraphValidationError(field + " must include a timezone")
    return parsed


def _covers(item: Dict[str, Any], when: datetime) -> bool:
    return _instant(item["valid_from"], "valid_from") <= when and (
        item["valid_to"] is None or when < _instant(item["valid_to"], "valid_to"))


def _json_hash(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise MemoryGraphValidationError("payload is not canonical JSON: %s" % error)
    return hashlib.sha256(raw).hexdigest()


def _text(value: Any, field: str, limit: int = 1024) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise MemoryGraphValidationError("%s must be non-empty text" % field)
    return value


def _refs(value: Any, field: str, limit: int = 64) -> List[str]:
    if (not isinstance(value, list) or len(value) > limit or len(value) != len(set(value))
            or any(not isinstance(v, str) or not v for v in value)):
        raise MemoryGraphValidationError("%s must be a unique string list" % field)
    return list(value)


def _interval(item: Dict[str, Any]) -> None:
    start = _instant(item.get("valid_from"), "valid_from")
    end = item.get("valid_to")
    if end is not None and _instant(end, "valid_to") <= start:
        raise MemoryGraphValidationError("valid_to must be after valid_from")


def _stored_shape(item: Any) -> bool:
    if not isinstance(item, dict) or item.get("record_type") not in RECORD_TYPES:
        return False
    kind = item["record_type"]
    if set(item) != _COMMON | _FIELDS[kind] or item.get("schema_version") != 1:
        return False
    if (type(item.get("event_seq")) is not int or item["event_seq"] < 1
            or type(item.get("batch_index")) is not int or type(item.get("batch_size")) is not int
            or item["batch_size"] < 1 or not 0 <= item["batch_index"] < item["batch_size"]
            or not _ID_PATTERNS["record_id"].fullmatch(str(item.get("record_id", "")))
            or not isinstance(item.get("operation_id"), str) or not item["operation_id"]
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("payload_hash", "")))):
        return False
    try:
        _instant(item.get("tx_time"), "tx_time"); _validate_payload(kind, item, replay=True)
    except MemoryGraphValidationError:
        return False
    return True


def _validate_payload(kind: str, item: Dict[str, Any], replay: bool = False) -> Dict[str, Any]:
    if not isinstance(item, dict) or (not replay and set(item) != _FIELDS[kind]):
        raise MemoryGraphValidationError("%s payload has invalid shape" % kind)
    out = {key: item[key] for key in _FIELDS[kind]}
    _interval(out)
    if kind == "claim":
        if not _ID_PATTERNS["claim_id"].fullmatch(str(out["claim_id"])):
            raise MemoryGraphValidationError("claim_id has invalid shape")
        out["canonical_key"] = _canonical(out["canonical_key"])
        _text(out["statement"], "statement", 10000)
        if out["scope"] is not None: _text(out["scope"], "scope", 256)
        out["artifact_refs"] = _refs(out["artifact_refs"], "artifact_refs")
        if out["provenance"] is not None and not isinstance(out["provenance"], dict):
            raise MemoryGraphValidationError("provenance must be a dict or null")
    elif kind == "relation":
        if not _ID_PATTERNS["relation_id"].fullmatch(str(out["relation_id"])):
            raise MemoryGraphValidationError("relation_id has invalid shape")
        if out["relation_type"] not in RELATION_TYPES:
            raise MemoryGraphValidationError("relation_type is outside its closed enum")
        for field in ("src_claim_id", "dst_claim_id"):
            if not _ID_PATTERNS["claim_id"].fullmatch(str(out[field])):
                raise MemoryGraphValidationError(field + " has invalid shape")
        out["evidence_artifact_refs"] = _refs(out["evidence_artifact_refs"], "evidence_artifact_refs")
        out["correction_event_refs"] = _refs(out["correction_event_refs"], "correction_event_refs")
    else:
        if not _ID_PATTERNS["claim_id"].fullmatch(str(out["claim_id"])):
            raise MemoryGraphValidationError("claim_id has invalid shape")
        if out["state"] not in LIFECYCLE_STATES:
            raise MemoryGraphValidationError("state is outside its closed enum")
        _text(out["caused_by"], "caused_by", 256); _text(out["reason"], "reason", 1024)
        out["evidence_refs"] = _refs(out["evidence_refs"], "evidence_refs")
    return out


def _empty_state() -> Dict[str, Any]:
    return {"records": [], "claims": {}, "relations": [], "lifecycles": {}, "operations": {}}


def _replay(path: Path) -> Dict[str, Any]:
    state = _empty_state()
    try: raw = path.read_bytes()
    except FileNotFoundError: return state
    if raw and not raw.endswith(b"\n"):
        raise MemoryGraphCorruptError("torn final memory graph line")
    batches: Dict[str, List[Dict[str, Any]]] = {}
    for number, line in enumerate(raw.splitlines(), 1):
        if not line:
            raise MemoryGraphCorruptError("blank memory graph line")
        try: item = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise MemoryGraphCorruptError("malformed memory graph line %d" % number)
        if not _stored_shape(item) or item["event_seq"] != number:
            raise MemoryGraphCorruptError("invalid schema or sequence at line %d" % number)
        batches.setdefault(item["operation_id"], []).append(item)
        state["records"].append(item)
    for operation_id, rows in batches.items():
        rows.sort(key=lambda r: r["batch_index"])
        size, digest = rows[0]["batch_size"], rows[0]["payload_hash"]
        if (len(rows) != size or [r["batch_index"] for r in rows] != list(range(size))
                or any(r["batch_size"] != size or r["payload_hash"] != digest for r in rows)
                or [r["event_seq"] for r in rows] != list(range(rows[0]["event_seq"], rows[0]["event_seq"] + size))):
            raise MemoryGraphCorruptError("incomplete or mismatched operation batch")
        payload = {"claims": [], "relations": [], "lifecycles": []}
        plural = {"claim": "claims", "relation": "relations", "lifecycle": "lifecycles"}
        for row in rows:
            payload[plural[row["record_type"]]].append(
                {key: row[key] for key in _FIELDS[row["record_type"]]})
        if _json_hash(payload) != digest:
            raise MemoryGraphCorruptError("operation payload hash mismatch")
        tx_times = {row["tx_time"] for row in rows}
        if len(tx_times) != 1:
            raise MemoryGraphCorruptError("operation transaction time mismatch")
        state["operations"][operation_id] = (digest, {
            "operation_id": operation_id, "event_seq_first": rows[0]["event_seq"],
            "event_seq_last": rows[-1]["event_seq"],
            "record_ids": [r["record_id"] for r in rows], "replayed": False})
    seen_claims, seen_relations, seen_records = set(), set(), set()
    for item in state["records"]:
        if item["record_id"] in seen_records:
            raise MemoryGraphCorruptError("duplicate record_id")
        seen_records.add(item["record_id"])
        kind = item["record_type"]
        if kind == "claim":
            if item["claim_id"] in seen_claims: raise MemoryGraphCorruptError("duplicate claim_id")
            seen_claims.add(item["claim_id"]); state["claims"][item["claim_id"]] = item
        elif kind == "relation":
            if item["relation_id"] in seen_relations or item["src_claim_id"] not in seen_claims or item["dst_claim_id"] not in seen_claims:
                raise MemoryGraphCorruptError("invalid relation identity or reference")
            seen_relations.add(item["relation_id"]); state["relations"].append(item)
        else:
            if item["claim_id"] not in seen_claims: raise MemoryGraphCorruptError("invalid lifecycle reference")
            state["lifecycles"].setdefault(item["claim_id"], []).append(item)
    return state


class MemoryGraphMixin:
    memory_graph_lock_timeout = 2.0

    def _memory_graph_dir(self) -> Path: return Path(self.base_dir) / ".memkraft"
    def _memory_graph_path(self) -> Path: return self._memory_graph_dir() / "memory_graph.jsonl"

    def _memory_graph_lock(self) -> int:
        directory = self._memory_graph_dir(); directory.mkdir(parents=True, exist_ok=True)
        path = directory / "memory_graph.lock"
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.memory_graph_lock_timeout
        if fcntl is None:
            return fd
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise MemoryGraphLockTimeoutError("memory graph lock timeout")
                time.sleep(0.01)

    def _memory_graph_unlock(self, fd: int) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def memory_graph_high_water(self) -> int:
        return len(_replay(self._memory_graph_path())["records"])

    def memory_graph_replay_check(self) -> Dict[str, int]:
        state = _replay(self._memory_graph_path())
        return {"records": len(state["records"]), "high_water": len(state["records"]),
                "operations": len(state["operations"])}

    def _memory_graph_correction_ids(self) -> set:
        path = Path(self.base_dir) / ".memkraft" / "corrections.jsonl"
        records, skipped = _read_correction_ledger(path)
        if skipped:
            raise MemoryGraphValidationError("correction ledger is not structurally valid")
        return {item["id"] for item in records}

    def memory_graph_append(self, *, operation_id: str, expected_high_water: int,
            claims: list = (), relations: list = (), lifecycles: list = (),
            max_records: int = 64, max_claims: int = 32, max_relations: int = 32) -> Dict[str, Any]:
        _text(operation_id, "operation_id", 128)
        if type(expected_high_water) is not int or expected_high_water < 0:
            raise MemoryGraphValidationError("expected_high_water must be a non-negative integer")
        if any(type(v) is not int or v < 0 for v in (max_records, max_claims, max_relations)):
            raise MemoryGraphValidationError("limits must be non-negative integers")
        if not all(isinstance(v, (list, tuple)) for v in (claims, relations, lifecycles)):
            raise MemoryGraphValidationError("record batches must be lists")
        prepared = []
        normalized_payload = {"claims": [], "relations": [], "lifecycles": []}
        for kind, values, plural in (
                ("claim", claims, "claims"),
                ("relation", relations, "relations"),
                ("lifecycle", lifecycles, "lifecycles")):
            for value in values:
                normalized = _validate_payload(kind, value)
                prepared.append((kind, normalized))
                normalized_payload[plural].append(normalized)
        digest = _json_hash(normalized_payload)
        fd = self._memory_graph_lock()
        try:
            state = _replay(self._memory_graph_path())
            prior = state["operations"].get(operation_id)
            if prior:
                if prior[0] != digest: raise MemoryGraphDuplicateOperationError("operation_id payload mismatch")
                result = dict(prior[1]); result["replayed"] = True; return result
            if expected_high_water != len(state["records"]):
                raise MemoryGraphCASError("expected_high_water is stale")
            count = len(claims) + len(relations) + len(lifecycles)
            if count > max_records or len(claims) > max_claims or len(relations) > max_relations:
                raise MemoryGraphLimitError("declared memory graph limit exceeded")
            if count == 0: raise MemoryGraphValidationError("batch must contain at least one record")
            known_claims = set(state["claims"]); known_relations = {r["relation_id"] for r in state["relations"]}
            artifacts = {}
            correction_ids = self._memory_graph_correction_ids()
            for kind, item in prepared:
                if kind == "claim":
                    if item["claim_id"] in known_claims: raise MemoryGraphValidationError("duplicate claim_id")
                    known_claims.add(item["claim_id"])
                    for ref in item["artifact_refs"]:
                        artifact = self.artifact_lookup(ref)
                        if artifact is None: raise MemoryGraphValidationError("artifact_ref does not resolve")
                        artifacts[ref] = artifact
                    if item["provenance"] is not None and not any(
                            artifact["provenance"] == item["provenance"] for artifact in artifacts.values()
                            if artifact["source_handle"].split(":", 1)[1] in item["artifact_refs"]):
                        raise MemoryGraphValidationError("provenance does not match a referenced artifact")
                elif kind == "relation":
                    if item["relation_id"] in known_relations: raise MemoryGraphValidationError("duplicate relation_id")
                    known_relations.add(item["relation_id"])
                    if item["src_claim_id"] not in known_claims or item["dst_claim_id"] not in known_claims:
                        raise MemoryGraphValidationError("relation claim reference does not resolve")
                    for ref in item["evidence_artifact_refs"]:
                        if self.artifact_lookup(ref) is None: raise MemoryGraphValidationError("evidence artifact does not resolve")
                    if any(ref not in correction_ids for ref in item["correction_event_refs"]):
                        raise MemoryGraphValidationError("correction event reference does not resolve")
                elif item["claim_id"] not in known_claims:
                    raise MemoryGraphValidationError("lifecycle claim reference does not resolve")
                if kind == "lifecycle":
                    for ref in item["evidence_refs"]:
                        if self.artifact_lookup(ref) is None and ref not in correction_ids:
                            raise MemoryGraphValidationError("lifecycle evidence reference does not resolve")
            now = datetime.now(timezone.utc).isoformat(); start = len(state["records"]) + 1
            rows = []
            for index, (kind, payload) in enumerate(prepared):
                envelope = {"schema_version": 1, "event_seq": start + index,
                    "record_id": "mg-" + os.urandom(8).hex(), "record_type": kind,
                    "operation_id": operation_id, "batch_index": index, "batch_size": count,
                    "payload_hash": digest, "tx_time": now}
                envelope.update(payload); rows.append(envelope)
            buffer = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8") for row in rows)
            path = self._memory_graph_path()
            out = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            original_size = os.fstat(out).st_size
            try:
                offset = 0
                while offset < len(buffer):
                    written = os.write(out, buffer[offset:])
                    if written <= 0:
                        raise OSError("short memory graph write")
                    offset += written
                os.fsync(out)
            except BaseException:
                os.ftruncate(out, original_size)
                os.fsync(out)
                raise
            finally: os.close(out)
            return {"operation_id": operation_id, "event_seq_first": start,
                    "event_seq_last": start + count - 1,
                    "record_ids": [r["record_id"] for r in rows], "replayed": False}
        finally: self._memory_graph_unlock(fd)

    def _memory_graph_state(self, state: Dict[str, Any], claim_id: str,
                            valid: datetime, tx: datetime) -> tuple:
        choices = [item for item in state["lifecycles"].get(claim_id, [])
                   if _instant(item["tx_time"], "tx_time") <= tx and _covers(item, valid)]
        if not choices: return "active", None
        item = max(choices, key=lambda x: x["event_seq"]); return item["state"], item

    def _memory_graph_provenance(self, claim: Dict[str, Any], lifecycle=None,
                                 prefix=None, relations=None) -> List[Dict[str, Any]]:
        path = list(prefix or [])
        for rel in relations or []:
            path.append({"kind": "relation", "id": rel["relation_id"],
                         "detail": rel["relation_type"]})
        path.append({"kind": "claim", "id": claim["claim_id"], "detail": claim["statement"]})
        if claim["provenance"] is not None:
            path.extend({"kind": "artifact", "id": ref, "detail": "referenced provenance"}
                        for ref in claim["artifact_refs"])
        else: path.append({"kind": "artifact", "id": None, "detail": "provenance absent"})
        if lifecycle:
            corrections = set(self._memory_graph_correction_ids())
            refs = [lifecycle.get("caused_by")] + list(lifecycle.get("evidence_refs") or [])
            path.extend({"kind": "correction_event", "id": ref, "detail": "lifecycle cause"}
                        for ref in refs if ref in corrections)
        return path

    def memory_graph_get_claim(self, claim_id: str, *, as_of_valid: Optional[str] = None,
                               as_of_tx: Optional[str] = None) -> Optional[Dict[str, Any]]:
        state = _replay(self._memory_graph_path()); claim = state["claims"].get(claim_id)
        if claim is None: return None
        valid = _instant(as_of_valid, "as_of_valid") if as_of_valid else datetime.now(timezone.utc)
        tx = _instant(as_of_tx, "as_of_tx") if as_of_tx else datetime.now(timezone.utc)
        if _instant(claim["tx_time"], "tx_time") > tx or not _covers(claim, valid): return None
        status, lifecycle = self._memory_graph_state(state, claim_id, valid, tx)
        payload = {key: claim[key] for key in _FIELDS["claim"]}
        return {"claim": payload, "state": status,
                "provenance_path": self._memory_graph_provenance(claim, lifecycle)}

    def memory_graph_recall(self, query: str, *, scope: Optional[str] = None,
            include_inactive: bool = False, as_of_valid: Optional[str] = None,
            max_hops: int = 2, limit: int = 20) -> List[Dict[str, Any]]:
        normalized = _canonical(query)
        if type(max_hops) is not int or not 0 <= max_hops <= 16 or type(limit) is not int or limit < 1:
            raise MemoryGraphValidationError("max_hops/limit outside bounded domain")
        state = _replay(self._memory_graph_path()); valid = (
            _instant(as_of_valid, "as_of_valid") if as_of_valid else datetime.now(timezone.utc))
        tx = datetime.now(timezone.utc)
        eligible = {cid: claim for cid, claim in state["claims"].items()
                    if _covers(claim, valid) and (scope is None or claim["scope"] == scope)}
        candidates = {}
        def add(cid, tier, hops, via, chain):
            if cid not in eligible: return False
            status, lifecycle = self._memory_graph_state(state, cid, valid, tx)
            if status == "retired" or (tier >= 2 and status != "active" and not include_inactive): return False
            prior = candidates.get(cid)
            value = (tier, hops, via, chain, status, lifecycle)
            if prior is None or (tier, hops, [r["relation_id"] for r in chain]) < (prior[0], prior[1], [r["relation_id"] for r in prior[3]]):
                candidates[cid] = value
            return True
        seeds = set()
        for cid, claim in eligible.items():
            if claim["canonical_key"] == normalized: add(cid, 0, 0, "canonical_key", []); seeds.add(cid)
        artifacts = self.search_artifacts(query, exact_phrase=True)
        artifact_ids = {a["source_handle"].split(":", 1)[1] for a in artifacts}
        for cid, claim in eligible.items():
            if artifact_ids.intersection(claim["artifact_refs"]):
                if add(cid, 1, 0, "artifact", []): seeds.add(cid)
            if normalized in _canonical(claim["statement"]):
                if add(cid, 2, 0, "statement", []): seeds.add(cid)
        adjacency = {}
        for rel in state["relations"]:
            if not _covers(rel, valid): continue
            adjacency.setdefault(rel["src_claim_id"], []).append((rel["dst_claim_id"], rel))
            adjacency.setdefault(rel["dst_claim_id"], []).append((rel["src_claim_id"], rel))
        queue = [(seed, 0, []) for seed in sorted(seeds)]; visited = {seed: 0 for seed in seeds}
        while queue:
            node, hops, chain = queue.pop(0)
            if hops >= max_hops: continue
            for neighbor, rel in sorted(adjacency.get(node, []), key=lambda x: (x[1]["relation_id"], x[0])):
                distance = hops + 1
                if neighbor in visited and visited[neighbor] <= distance: continue
                visited[neighbor] = distance; next_chain = chain + [rel]
                add(neighbor, 3 if distance == 1 else 4, distance, "relation", next_chain)
                queue.append((neighbor, distance, next_chain))
        rank_state = {"active": 0, "superseded": 1, "contradicted": 2}
        ranked = []
        for cid, (tier, hops, via, chain, status, lifecycle) in candidates.items():
            claim = eligible[cid]
            key = (tier, rank_state.get(status, 3), hops,
                   -_instant(claim["valid_from"], "valid_from").timestamp(),
                   -claim["event_seq"], claim["record_id"])
            prefix = [{"kind": "claim", "id": normalized, "detail": via + " query match"}]
            result = {"claim": {k: claim[k] for k in _FIELDS["claim"]}, "state": status,
                "tier": tier, "hops": hops, "matched_via": via,
                "relation_chain": [r["relation_id"] for r in chain],
                "provenance_path": self._memory_graph_provenance(claim, lifecycle, prefix, chain)}
            ranked.append((key, result))
        ranked.sort(key=lambda item: item[0]); return [item[1] for item in ranked[:limit]]


__all__ = ["MemoryGraphMixin", "MemoryGraphError", "MemoryGraphValidationError",
    "MemoryGraphLimitError", "MemoryGraphCASError", "MemoryGraphDuplicateOperationError",
    "MemoryGraphCorruptError", "MemoryGraphLockTimeoutError", "RECORD_TYPES",
    "RELATION_TYPES", "LIFECYCLE_STATES"]
