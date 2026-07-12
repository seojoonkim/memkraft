"""Tiny deterministic external-format adapter; no network, model, or judge."""
from __future__ import annotations
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

_FIXTURE = Path(__file__).with_name("fixtures") / "sample.json"


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


class SampleAdapter:
    name = "external_sample"

    @property
    def dataset_version(self) -> str:
        return "sha256:" + hashlib.sha256(_FIXTURE.read_bytes()).hexdigest()

    def load_cases(self) -> list[dict[str, Any]]:
        return json.loads(_FIXTURE.read_text(encoding="utf-8"))

    def ingest(self, mk: Any, case: dict[str, Any]) -> None:
        for index, text in enumerate(case["memories"]):
            name = f"external-{case['case_id']}-{index}"
            mk.track(name, source="external-sample")
            mk.update(name, text, source="external-sample")

    def answer(self, mk: Any, case: dict[str, Any]) -> str:
        hits = mk.search(case["question"], fuzzy=True, top_k=5)
        expected = _normalize(case["expected"])
        for hit in hits:
            snippet = str(hit.get("snippet", ""))
            if expected in _normalize(snippet):
                return case["expected"]
        return ""

    def score(self, predicted: str, expected: str, match: str) -> bool:
        if match not in {"exact", "normalized", "contains"}:
            raise ValueError(f"unsupported match: {match}")
        if match == "exact":
            return predicted == expected
        if match == "contains":
            return _normalize(expected) in _normalize(predicted)
        return _normalize(predicted) == _normalize(expected)


def run_sample(adapter: Optional[SampleAdapter] = None) -> dict[str, Any]:
    from memkraft import MemKraft
    adapter = adapter or SampleAdapter()
    per_case = []
    for case in adapter.load_cases():
        with tempfile.TemporaryDirectory() as tmp:
            mk = MemKraft(tmp)
            adapter.ingest(mk, case)
            predicted = adapter.answer(mk, case)
            per_case.append({"case_id": case["case_id"], "passed": adapter.score(predicted, case["expected"], case["match"])})
    accuracy = sum(row["passed"] for row in per_case) / len(per_case)
    return {"scenario": adapter.name, "dataset_version": adapter.dataset_version,
            "network_required": False, "model_required": False,
            "observed": {"accuracy": accuracy}, "per_case": per_case}
