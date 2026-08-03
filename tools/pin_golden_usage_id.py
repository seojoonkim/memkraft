"""Pin the golden ``usage_id`` that MKEP/0 must not disturb (plan §19.2 Slice 0).

`compile_context` derives `usage_id` as a sha256 over a fixed identity dict
(assumption A3). Slice 11 adds `goal_id` / `execution_budget` parameters, and
the release gate G10 forbids those parameters from changing the hash when the
execution section is not produced. This module builds a fully deterministic
base, records the resulting `usage_id`, and writes it to
``tests/golden/usage_id.json`` so the guard test can compare byte-for-byte.

Run: ``python tools/pin_golden_usage_id.py``
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_PATH = REPO_ROOT / "tests" / "golden" / "usage_id.json"

# Every value here is fixed; nothing derives from the clock, the filesystem, or
# a random id. `compile_context` strips record `id` from the identity dict, so
# store-assigned uuid4 values cannot leak into the hash.
SCENARIO: Dict[str, Any] = {
    "task": "ship the execution kernel",
    "budget": 512,
    "objective": "MKEP/0 preview",
    "events": [
        {
            "subject_id": "release",
            "key": "target_version",
            "value": "3.3.0",
            "source": "plan",
            "valid_from": "2026-08-04T00:00:00+00:00",
        },
        {
            "subject_id": "release",
            "key": "baseline_commit",
            "value": "b9453da",
            "source": "plan",
            "valid_from": "2026-08-04T00:00:01+00:00",
        },
        {
            "subject_id": "kernel",
            "key": "storage",
            "value": "store_core append under flock",
            "source": "store_core",
            "valid_from": "2026-08-04T00:00:02+00:00",
        },
    ],
}


def compute_golden() -> Dict[str, Any]:
    """Build the pinned scenario in a throwaway base and return the golden record."""
    if str(REPO_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "src"))
    from memkraft import MemKraft

    with tempfile.TemporaryDirectory() as tmp:
        mk = MemKraft(base_dir=tmp)
        mk.init()
        for event in SCENARIO["events"]:
            mk.append_event(**event)
        result = mk.compile_context(
            SCENARIO["task"],
            SCENARIO["budget"],
            objective=SCENARIO["objective"],
        )
    return {
        "scenario": SCENARIO,
        "usage_id": result["usage_id"],
        # Store-assigned uuid4 `id`s are not reproducible, and `compile_context`
        # strips them before hashing. Pin the identity shape, not the raw one.
        "identity_sections": identity_sections(result["sections"]),
        "sources": result["sources"],
        "estimated_tokens": result["estimated_tokens"],
    }


def identity_sections(sections: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``sections`` with record ``id`` removed, as the hash sees it."""
    return {
        name: [{k: v for k, v in item.items() if k != "id"} for item in items]
        for name, items in sections.items()
    }


def main() -> int:
    golden = compute_golden()
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        json.dumps(golden, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {GOLDEN_PATH.relative_to(REPO_ROOT)}: {golden['usage_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
