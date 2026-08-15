"""Pure evidence reducer and deterministic snapshot identities."""
import json
from typing import Any, Dict, Iterable, List

from .digest import canonical_json, digest_fields
from .model import COMPILER_SCHEMA, normalize_as_of


def _jsonl(rows: Iterable[Dict[str, Any]]) -> bytes:
    return b"".join((canonical_json(row) + "\n").encode("utf-8") for row in rows)


def evidence_bytes(observations: Iterable[Dict[str, Any]]) -> bytes:
    return _jsonl(sorted(observations, key=lambda row: row["observation_id"]))


def observations_from_evidence(raw: bytes) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]


def reduce_observations(observations: Iterable[Dict[str, Any]], *,
                        as_of: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    canonical_as_of = normalize_as_of(as_of)
    observations = sorted(list(observations), key=lambda row: row["observation_id"])
    sections = []
    for observation in observations:
        locator = observation["locator"]
        sections.append({
            "schema_version": 1,
            "section_id": digest_fields(locator["path"], locator["lines"],
                                         observation["heading_path"],
                                         observation["content_digest"]),
            "observation_id": observation["observation_id"],
            "heading_path": observation["heading_path"],
            "locator": locator,
            "content_digest": observation["content_digest"],
        })
    sections.sort(key=lambda row: row["section_id"])
    sections_raw = _jsonl(sections)
    semantic_digest = digest_fields(COMPILER_SCHEMA, *[canonical_json(row) for row in sections])
    pairs = [[key, config[key]] for key in sorted(config)]
    config_digest = digest_fields(*[canonical_json(pair) for pair in pairs])
    return {"observations": observations, "sections": sections,
            "evidence_bytes": evidence_bytes(observations), "sections_bytes": sections_raw,
            "semantic_digest": semantic_digest, "config_digest": config_digest,
            "snapshot_id": digest_fields(semantic_digest, config_digest,
                                         COMPILER_SCHEMA, canonical_as_of),
            "as_of": canonical_as_of}
