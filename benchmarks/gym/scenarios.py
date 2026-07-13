"""Scenario adapters for MemKraft Memory Gym."""
from __future__ import annotations

import contextlib
import io
import math
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, cast

from benchmarks.search_recall_bench import run_one as run_search_recall_one
from benchmarks.gym.gates import (
    MAX_LAST_INTERACTION_P95_MS,
    MAX_SESSION_OVERLAY_EXPIRED_EXPOSURES,
    MAX_SESSION_OVERLAY_GOVERNED_EXPOSURES,
    MAX_SESSION_OVERLAY_LEAKS,
    MIN_CLAIM_EXTRACTION_ACCURACY,
    MIN_KO_SEARCH_RECALL_AT_K,
    MIN_MIN_RECALL_AT_K,
    MIN_RESOLVER_VERDICT_ACCURACY,
    MIN_SEARCH_PRECISION_AT_K,
    MIN_SESSION_OVERLAY_FREE_TEXT_DISCOVERIES,
    MIN_SESSION_OVERLAY_RECALL,
    MIN_TRUTH_FRESHNESS_CONTRACT,
)

ScenarioRunner = Callable[..., dict[str, Any]]

# Registration point: new Gym scenarios (session_overlay_recall, resolver_verdicts, ...) register here.
_SCENARIOS: dict[str, ScenarioRunner] = {}


def register_scenario(name: str, runner: ScenarioRunner) -> None:
    """Register a Memory Gym scenario runner under a unique name."""
    label = str(name).strip()
    if not label:
        raise ValueError("Memory Gym scenario name must be a non-empty string")
    if not callable(runner):
        raise ValueError(f"Memory Gym scenario runner for {label!r} must be callable")
    _SCENARIOS[label] = runner


def registered_scenarios() -> list[str]:
    """Return the sorted names of all registered Memory Gym scenarios."""
    return sorted(_SCENARIOS)


def _hybrid_search(alpha: float):
    def search(mk: Any, query: str, top_k: int) -> list[dict[str, Any]]:
        search_hybrid = getattr(mk, "search_hybrid", None)
        if not callable(search_hybrid):
            raise ValueError("hybrid candidate requires MemKraft.search_hybrid")
        return cast(list[dict[str, Any]], search_hybrid(query, top_k=top_k, alpha=alpha))

    return search


def run_scenario(
    scenario: str,
    sizes: Iterable[int],
    top_k: int = 20,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
    """Run a named Memory Gym scenario and return a JSON-serialisable payload."""
    runner = _SCENARIOS.get(scenario)
    if runner is None:
        raise ValueError(f"unknown Memory Gym scenario: {scenario}")
    return runner(sizes=sizes, top_k=top_k, candidate=candidate, hybrid_alpha=hybrid_alpha)


def _run_search_recall(
    *,
    sizes: Iterable[int],
    top_k: int = 20,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
    candidate_label = str(candidate).strip().lower()
    if candidate_label not in {"baseline", "legacy", "hybrid"}:
        raise ValueError(f"unknown Memory Gym candidate: {candidate}")

    parsed_sizes = [int(size) for size in sizes]
    parsed_top_k = int(top_k)
    if any(size <= 0 for size in parsed_sizes):
        raise ValueError("Memory Gym scenario sizes must be positive integers")
    if parsed_top_k <= 0:
        raise ValueError("Memory Gym scenario top_k must be positive")
    parsed_hybrid_alpha = float(hybrid_alpha)
    if not math.isfinite(parsed_hybrid_alpha) or not 0.0 <= parsed_hybrid_alpha <= 1.0:
        raise ValueError("Memory Gym scenario hybrid_alpha must be finite and between 0.0 and 1.0")

    candidate_fn = _hybrid_search(parsed_hybrid_alpha) if candidate_label == "hybrid" else None
    payload = {
        "scenario": "search_recall",
        "candidate": candidate_label,
        "top_k": parsed_top_k,
        "results": [
            run_search_recall_one(size=size, top_k=parsed_top_k, candidate_fn=candidate_fn)
            for size in parsed_sizes
        ],
    }
    if candidate_label == "hybrid":
        payload["hybrid_alpha"] = parsed_hybrid_alpha
    return payload


def _run_session_overlay_recall(
    *,
    sizes: Iterable[int],
    top_k: int = 5,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
    from memkraft import MemKraft

    parsed_sizes = [int(size) for size in sizes]
    parsed_top_k = int(top_k)
    if any(size <= 0 for size in parsed_sizes):
        raise ValueError("Memory Gym scenario sizes must be positive integers")
    if parsed_top_k <= 0:
        raise ValueError("Memory Gym scenario top_k must be positive")

    results = []
    for size in parsed_sizes:
        with tempfile.TemporaryDirectory() as tmp:
            mk = MemKraft(str(Path(tmp)))
            for i in range(size):
                mk.remember_candidate(f"session alpha topic {i}", session_id="alpha")
                mk.remember_candidate(f"session beta topic {i}", session_id="beta")
            expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
            mk.remember_candidate("session alpha expired sentinel", session_id="alpha", expires_at=expired)
            mk.remember_candidate("GovernedUser prefers governed sentinel", session_id="alpha")
            mk.remember_candidate("governed sentinel free text", session_id="alpha")
            mk.do_not_remember(subject="GovernedUser", key="prefers", dry_run=False)

            found = 0
            leaks = 0
            expired_hits = 0
            for i in range(size):
                hits = mk.session_overlay("alpha", f"topic {i}", top_k=parsed_top_k)
                if any(hit["text"] == f"session alpha topic {i}" for hit in hits):
                    found += 1
                leaks += sum(1 for hit in hits if hit.get("session_id") != "alpha")
                expired_hits += sum(1 for hit in hits if "expired sentinel" in hit.get("text", ""))

            governed_hits = sum(
                hit.get("text") == "GovernedUser prefers governed sentinel"
                for hit in mk.session_overlay(
                    "alpha", "GovernedUser prefers governed sentinel", top_k=parsed_top_k
                )
            )
            free_text_discoveries = sum(
                hit.get("text") == "governed sentinel free text"
                for hit in mk.session_overlay(
                    "alpha", "governed sentinel free text", top_k=parsed_top_k
                )
            )

            same_session_recall = found / size
            results.append(
                {
                    "documents": size,
                    "same_session_recall": same_session_recall,
                    "cross_session_leaks": leaks,
                    "expired_exposures": expired_hits,
                    "governed_exposures": governed_hits,
                    "governed_free_text_discoveries": free_text_discoveries,
                    "thresholds": {
                        "min_same_session_recall": MIN_SESSION_OVERLAY_RECALL,
                        "max_cross_session_leaks": MAX_SESSION_OVERLAY_LEAKS,
                        "max_expired_exposures": MAX_SESSION_OVERLAY_EXPIRED_EXPOSURES,
                        "max_governed_exposures": MAX_SESSION_OVERLAY_GOVERNED_EXPOSURES,
                        "min_governed_free_text_discoveries": MIN_SESSION_OVERLAY_FREE_TEXT_DISCOVERIES,
                    },
                }
            )
    return {"scenario": "session_overlay_recall", "top_k": parsed_top_k, "results": results}


def _run_resolver_verdicts(
    *,
    sizes: Iterable[int],
    top_k: int = 5,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
    import json

    from memkraft.resolver import resolver_dry_run

    fixture = Path(__file__).parents[2] / "tests" / "fixtures" / "resolver_cases.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    parsed_sizes = [int(size) for size in sizes]
    results = []
    for size in parsed_sizes:
        subset = cases[:size]
        outputs = resolver_dry_run([case["claim"] for case in subset])
        correct = sum(out["verdict"] == case["expected_verdict"] for out, case in zip(outputs, subset))
        repeated = resolver_dry_run([case["claim"] for case in subset])
        missing_promotions = sum(
            1
            for out, case in zip(outputs, subset)
            if case["expected_verdict"] == "MISSING_SOURCE_REJECT" and out["can_promote"]
        )
        results.append(
            {
                "documents": len(subset),
                "accuracy": correct / len(subset) if subset else 0.0,
                "determinism": 1.0 if repeated == outputs else 0.0,
                "missing_source_promotions": missing_promotions,
                "thresholds": {"min_accuracy": MIN_RESOLVER_VERDICT_ACCURACY},
            }
        )
    return {"scenario": "resolver_verdicts", "results": results}


def _run_last_interaction(
    *,
    sizes: Iterable[int],
    top_k: int = 5,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
    from memkraft import MemKraft

    parsed_sizes = [int(size) for size in sizes]
    results = []
    for size in parsed_sizes:
        with tempfile.TemporaryDirectory() as tmp:
            mk = MemKraft(str(Path(tmp)))
            base = datetime(2026, 7, 8, tzinfo=timezone.utc)
            for i in range(size):
                mk.record_interaction(
                    f"subject-{i:05d}",
                    (base + timedelta(seconds=i)).isoformat(),
                    f"interaction-{i:05d}",
                )
            ok = 0
            durations = []
            probes = min(200, size)
            for i in range(probes):
                subject = f"subject-{i:05d}"
                start = time.perf_counter()
                latest = mk.last_interaction(subject)
                durations.append((time.perf_counter() - start) * 1000)
                if latest and latest.get("interaction_id") == f"interaction-{i:05d}":
                    ok += 1
            durations.sort()
            p95 = durations[int(len(durations) * 0.95) - 1] if durations else 0.0
            results.append(
                {
                    "documents": size,
                    "accuracy": ok / probes if probes else 0.0,
                    "p95_ms": p95,
                    "thresholds": {"max_p95_ms": MAX_LAST_INTERACTION_P95_MS},
                }
            )
    return {"scenario": "last_interaction", "results": results}


def _run_lifecycle_governance(**_kwargs: Any) -> dict[str, Any]:
    from memkraft import MemKraft
    with tempfile.TemporaryDirectory() as tmp:
        mk = MemKraft(tmp)
        event = mk.append_event("user", "city", "Seoul", source="gym")
        dry = mk.sleep()
        applied = mk.sleep(dry_run=False)
        retry = mk.sleep(dry_run=False)
        rebuilt = mk.current_truth("user") == {"city": "Seoul"}
        mk.forget(event["id"], dry_run=False)
        propagated = mk.current_truth("user") == {} and mk.export_memory() == []
        passed = all((
            rebuilt,
            dry["transaction_id"] == applied["transaction_id"],
            retry["status"] == "already_applied",
            propagated,
        ))
        return {"scenario": "lifecycle_governance", "results": [{
            "truth_rebuild": rebuilt,
            "dry_apply_same_transaction": dry["transaction_id"] == applied["transaction_id"],
            "idempotent_retry": retry["status"] == "already_applied",
            "deletion_propagated": propagated,
            "mean_recall_at_k": float(passed),
            "min_recall_at_k": float(passed),
        }]}


def _run_outcome_context(**_kwargs: Any) -> dict[str, Any]:
    """Prove feedback reranking, reward clamp, and explicit pin protection."""
    from datetime import datetime, timezone
    from memkraft import MemKraft
    now = datetime(2026, 1, 31, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        mk = MemKraft(tmp)
        mk.append_event("u", "alpha", "A", source="source:a")
        mk.append_event("u", "beta", "B", source="source:b")
        mk.compile_truth(dry_run=False)
        before = mk.compile_context("gym", 500, now=now)
        report = mk.report_outcome(before["usage_id"], "failure", reward=-99, now=now)
        after = mk.compile_context("gym", 500, now=now)
        pinned = mk.compile_context("gym", 500, now=now, pinned_sources=["source:a"])
        changed = before["sections"]["truth"][0]["key"] != after["sections"]["truth"][0]["key"]
        clamped = report["reward"] == -1.0 and report["max_update"] == 0.2
        protected = pinned["sections"]["truth"][0]["source"] == "source:a"
        passed = changed and clamped and protected
        return {"scenario": "outcome_context", "results": [{
            "documents": 2, "ordering_changed": changed, "reward_clamped": clamped,
            "pin_protected": protected, "mean_recall_at_k": float(passed),
            "min_recall_at_k": float(passed),
        }]}


def _derived_rows_are_sourced(
    derived: list[dict[str, Any]], canonical: list[dict[str, Any]], transaction_id: str
) -> bool:
    """Require each derived claim to match canonical content, or its sleep transaction."""
    claims = {
        (r.get("subject_id"), r.get("key"), r.get("value"), r.get("source"), r.get("provenance"))
        for r in canonical if not r.get("tombstone")
    }
    for row in derived:
        if row.get("action") == "sleep":
            if row.get("source") != "sleep" or row.get("transaction_id") != transaction_id:
                return False
        elif (row.get("subject_id"), row.get("key"), row.get("value"),
              row.get("source"), row.get("provenance")) not in claims:
            return False
    return True


def run_lifecycle_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Replay an offline fixture exclusively through the public v3 lifecycle."""
    from memkraft import MemKraft
    from memkraft.store_core import read_all
    events = fixture.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("events must be a non-empty list")
    if any(not isinstance(e, dict) or not (e.get("source") or e.get("provenance")) for e in events):
        raise ValueError("source (or provenance) is required")
    with tempfile.TemporaryDirectory() as tmp:
        mk = MemKraft(tmp)
        fixture_to_actual = {}
        for event in events:
            row = mk.append_event(
                event.get("subject_id"), event.get("key"), event.get("value"),
                source=event.get("source"), provenance=event.get("provenance"),
                valid_from=event.get("valid_from"),
            )
            if event.get("id"):
                fixture_to_actual[event["id"]] = row["id"]
        mk.compile_truth(dry_run=False)
        dry, applied = mk.sleep(), mk.sleep(dry_run=False)
        forget_id = fixture_to_actual.get(fixture["forget_id"])
        if forget_id is None:
            raise ValueError("forget_id must reference a fixture event")
        forgotten_event = next(e for e in events if e.get("id") == fixture["forget_id"])
        forgotten = mk.forget(forget_id, dry_run=False)
        context = mk.compile_context(fixture["task"], fixture["budget"])
        outcome = fixture["outcome"]
        report = mk.report_outcome(context["usage_id"], outcome["label"], reward=outcome["reward"])
        canonical = read_all(mk._canonical_events_path(), True).records
        derived = read_all(mk._compiled_truth_path()).records + read_all(mk._sleep_journal_path()).records
        no_unsourced = _derived_rows_are_sourced(derived, canonical, applied["transaction_id"])
        recalled = mk.current_truth("ada") == fixture["expected_truth"]
        hidden_export = forget_id not in {row["id"] for row in mk.export_memory()} and forgotten["status"] == "applied"
        hidden_truth = forgotten_event["key"] not in mk.current_truth(forgotten_event["subject_id"])
        post_context = mk.compile_context(fixture["task"], fixture["budget"])
        context_rows = [row for rows in post_context["sections"].values() for row in rows]
        hidden_context = all(row.get("id") != forget_id and not (
            row.get("subject_id") == forgotten_event["subject_id"] and row.get("key") == forgotten_event["key"]
        ) for row in context_rows)
        forgotten_value = str(forgotten_event["value"])
        search_hits = mk.search(forgotten_value, fuzzy=True, top_k=20)
        hidden_search = all(
            forget_id not in str(hit) and forgotten_value.lower() not in str(hit.get("snippet", "")).lower()
            for hit in search_hits
        )
        hidden = hidden_export and hidden_truth and hidden_context and hidden_search
        stable = dry["transaction_id"] == applied["transaction_id"]
        outcome_recorded = report["usage_id"] == context["usage_id"]
        passed = all((recalled, hidden, no_unsourced, stable, outcome_recorded))
        return {"scenario": "lifecycle_replay", "fixture_schema": fixture["schema_version"], "results": [{
            "documents": len(events), "recall": recalled, "tombstone_hidden": hidden,
            "tombstone_hidden_current_truth": hidden_truth,
            "tombstone_hidden_context": hidden_context, "tombstone_hidden_search": hidden_search,
            "no_unsourced_derived_writes": no_unsourced,
            "sleep_transaction_stable": stable, "outcome_recorded": outcome_recorded,
            "mean_recall_at_k": float(passed), "min_recall_at_k": float(passed),
        }]}


def _parse_positive_sizes(sizes: Iterable[int]) -> list[int]:
    parsed = [int(size) for size in sizes]
    if not parsed or any(size <= 0 for size in parsed):
        raise ValueError("Memory Gym scenario sizes must be positive integers")
    return parsed


def _run_search_precision(
    *,
    sizes: Iterable[int],
    top_k: int = 20,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
    """Trap-document precision: recall-only inflation must fail this gate."""
    from memkraft import MemKraft

    parsed_sizes = _parse_positive_sizes(sizes)
    parsed_top_k = int(top_k)
    if parsed_top_k <= 0:
        raise ValueError("Memory Gym scenario top_k must be positive")
    if parsed_top_k < max(parsed_sizes):
        raise ValueError("search_precision top_k must be at least the relevant document count")

    results = []
    for size in parsed_sizes:
        traps = max(2, size)
        with tempfile.TemporaryDirectory() as tmp:
            mk = MemKraft(str(Path(tmp)))
            with contextlib.redirect_stdout(io.StringIO()):
                for i in range(size):
                    name = f"Precision Relevant {i:05d}"
                    mk.track(name, source="gym")
                    mk.update(name, info=f"precision_needle shared payload marker{i:05d}", source="gym")
                for i in range(traps):
                    name = f"Precision Trap {i:05d}"
                    mk.track(name, source="gym")
                    mk.update(name, info="precision trap needle decoy shared payload", source="gym")
                hits = mk.search("precision_needle", top_k=parsed_top_k, cache=False)
            returned = [str(hit.get("file", "")) for hit in hits]
            relevant_returned = {f for f in returned if "relevant" in f.lower()}
            precision = len([f for f in returned if "relevant" in f.lower()]) / len(returned) if returned else 0.0
            results.append(
                {
                    "documents": size,
                    "trap_documents": traps,
                    "returned_hits": len(returned),
                    "precision_at_k": precision,
                    "recall_at_k": len(relevant_returned) / size,
                    "thresholds": {
                        "min_precision_at_k": MIN_SEARCH_PRECISION_AT_K,
                        "min_recall_at_k": MIN_MIN_RECALL_AT_K,
                    },
                }
            )
    return {"scenario": "search_precision", "top_k": parsed_top_k, "results": results}


def _run_search_recall_ko(
    *,
    sizes: Iterable[int],
    top_k: int = 5,
    candidate: str = "baseline",
    hybrid_alpha: float = 0.025,
) -> dict[str, Any]:
    """Hangul retrieval: a Korean-token regression must drop recall to zero."""
    from memkraft import MemKraft

    parsed_sizes = _parse_positive_sizes(sizes)
    parsed_top_k = int(top_k)
    if parsed_top_k <= 0:
        raise ValueError("Memory Gym scenario top_k must be positive")

    # Pure-Hangul unique markers: a Hangul-token regression must leave nothing
    # searchable (ASCII digits in the marker would survive and mask the bug).
    hangul_digits = "공일이삼사오육칠팔구"

    def hangul_marker(index: int) -> str:
        return "한글마커" + "".join(hangul_digits[int(digit)] for digit in f"{index:04d}")

    results = []
    for size in parsed_sizes:
        with tempfile.TemporaryDirectory() as tmp:
            mk = MemKraft(str(Path(tmp)))
            with contextlib.redirect_stdout(io.StringIO()):
                for i in range(size):
                    name = f"KO Doc {i:04d}"
                    mk.track(name, source="gym")
                    mk.update(name, info=f"{hangul_marker(i)} 파이썬 메모리 검색 벤치마크", source="gym")
                found = 0
                for i in range(size):
                    hits = mk.search(hangul_marker(i), top_k=parsed_top_k, cache=False)
                    if any(f"{i:04d}" in str(hit.get("file", "")) for hit in hits):
                        found += 1
                broad_hits = mk.search("파이썬", top_k=parsed_top_k, cache=False)
            results.append(
                {
                    "documents": size,
                    "recall_at_k": found / size,
                    "broad_query_hit": 1.0 if broad_hits else 0.0,
                    "thresholds": {"min_recall_at_k": MIN_KO_SEARCH_RECALL_AT_K},
                }
            )
    return {"scenario": "search_recall_ko", "top_k": parsed_top_k, "results": results}


def _claim_matches(claims: list, expected: dict) -> bool:
    if len(claims) != 1 or not isinstance(claims[0], dict):
        return False
    claim_projection = {key: claims[0].get(key) for key in expected}
    return claim_projection == expected


def _run_claim_extraction(**_kwargs: Any) -> dict[str, Any]:
    """End-to-end EN/KO claim extraction through the remember_candidate preview API."""
    import json

    from memkraft import MemKraft

    fixture_path = Path(__file__).parents[2] / "tests" / "fixtures" / "claim_extraction_cases.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))["cases"]
    if not cases:
        raise ValueError("claim_extraction fixture must contain at least one case")

    positives = negatives = positives_ok = negatives_ok = 0
    failed_case_ids = []
    with tempfile.TemporaryDirectory() as tmp:
        mk = MemKraft(str(Path(tmp)))
        with contextlib.redirect_stdout(io.StringIO()):
            for case in cases:
                receipt = mk.remember_candidate(
                    case["text"], session_id="gym-claims", entity_hint=case.get("entity_hint")
                )
                records = [
                    record
                    for record in mk.list_candidates(session_id="gym-claims")
                    if record.get("candidate_id") == receipt["candidate_id"]
                ]
                record = records[0] if records else {}
                claims = record.get("claims") or []
                if case["expect_claims"]:
                    positives += 1
                    ok = bool(records) and record.get("review_state") == "READY_FOR_RESOLVER" and _claim_matches(
                        claims, case["expected_claim"]
                    )
                    positives_ok += ok
                else:
                    negatives += 1
                    ok = bool(records) and record.get("review_state") == "CANDIDATE_REVIEW" and claims == []
                    negatives_ok += ok
                if not ok:
                    failed_case_ids.append(case["id"])
    return {
        "scenario": "claim_extraction",
        "fixture": str(fixture_path.relative_to(Path(__file__).parents[2])),
        "results": [
            {
                "documents": len(cases),
                "positive_cases": positives,
                "negative_cases": negatives,
                "positive_accuracy": positives_ok / positives if positives else 0.0,
                "negative_accuracy": negatives_ok / negatives if negatives else 0.0,
                "failed_case_ids": failed_case_ids,
                "thresholds": {
                    "min_positive_accuracy": MIN_CLAIM_EXTRACTION_ACCURACY,
                    "min_negative_accuracy": MIN_CLAIM_EXTRACTION_ACCURACY,
                },
            }
        ],
    }


def _run_truth_freshness(**_kwargs: Any) -> dict[str, Any]:
    """Exercise compiled-truth freshness and fail-closed governance via public APIs."""
    from memkraft import MemKraft

    with tempfile.TemporaryDirectory() as tmp:
        mk = MemKraft(tmp)
        with contextlib.redirect_stdout(io.StringIO()):
            mk.append_event("user", "city", "old", source="gym")
            mk.sleep(dry_run=False)
            initial_status = mk.truth_status()
            initial_truth = mk.current_truth("user")

            mk.append_event("user", "city", "new", source="gym")
            append_status = mk.truth_status()
            stale_truth = mk.current_truth("user")

            mk.sleep(dry_run=False)
            sleep_status = mk.truth_status()
            refreshed_truth = mk.current_truth("user")

            mk.append_event("user", "secret", "deny-me", source="gym")
            mk.sleep(dry_run=False)
            policy_cached_truth = mk.current_truth("user")
            mk.do_not_remember(subject="user", key="secret", dry_run=False)
            policy_truth = mk.current_truth("user")

            disposable = mk.append_event("user", "temporary", "forget-me", source="gym")
            mk.sleep(dry_run=False)
            tombstone_cached_truth = mk.current_truth("user")
            mk.forget(disposable["id"], dry_run=False)
            tombstone_truth = mk.current_truth("user")
            mk.compact_memory(dry_run=False)
            compacted_truth = mk.current_truth("user")

        metrics = {
            "initial_status_fresh": float(initial_status["stale"] is False and initial_status["pending_event_count"] == 0),
            "initial_old_truth_visible": float(initial_truth == {"city": "old"}),
            "append_status_stale": float(append_status["stale"] is True),
            "append_pending_exactly_one": float(append_status["pending_event_count"] == 1),
            "stale_old_truth_preserved": float(stale_truth == {"city": "old"}),
            "sleep_status_fresh": float(sleep_status["stale"] is False and sleep_status["pending_event_count"] == 0),
            "sleep_new_truth_visible": float(refreshed_truth == {"city": "new"}),
            "policy_value_cached_before_governance": float(policy_cached_truth.get("secret") == "deny-me"),
            "policy_cached_value_hidden": float("secret" not in policy_truth),
            "tombstone_value_cached_before_forget": float(tombstone_cached_truth.get("temporary") == "forget-me"),
            "tombstoned_cached_value_hidden": float("temporary" not in tombstone_truth),
            "tombstoned_value_hidden_after_compaction": float("temporary" not in compacted_truth),
        }
        return {
            "scenario": "truth_freshness",
            "results": [
                {
                    "documents": 4,
                    **metrics,
                    "thresholds": {f"min_{key}": MIN_TRUTH_FRESHNESS_CONTRACT for key in metrics},
                }
            ],
        }


def _run_lifecycle_replay(**_kwargs: Any) -> dict[str, Any]:
    import json
    path = Path(__file__).with_name("fixtures") / "lifecycle_replay.json"
    return run_lifecycle_fixture(json.loads(path.read_text(encoding="utf-8")))


register_scenario("search_recall", _run_search_recall)
register_scenario("session_overlay_recall", _run_session_overlay_recall)
register_scenario("resolver_verdicts", _run_resolver_verdicts)
register_scenario("last_interaction", _run_last_interaction)
register_scenario("lifecycle_governance", _run_lifecycle_governance)
register_scenario("outcome_context", _run_outcome_context)
register_scenario("lifecycle_replay", _run_lifecycle_replay)
register_scenario("search_precision", _run_search_precision)
register_scenario("search_recall_ko", _run_search_recall_ko)
register_scenario("claim_extraction", _run_claim_extraction)
register_scenario("truth_freshness", _run_truth_freshness)
