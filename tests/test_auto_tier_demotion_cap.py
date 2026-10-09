"""auto_tier demotes at most one tier per run; promotions stay immediate."""
import pytest

from memkraft import MemKraft


@pytest.fixture
def mk(tmp_path):
    return MemKraft(base_dir=str(tmp_path))


def _never_accessed_core(mk):
    mk.track("Alice")
    mk.tier_set("alice", tier="core")
    # A score that maps to archival: no recency, no accesses, weak importance.
    return dict(recency_weight=1.0, frequency_weight=1.0, importance_weight=0.0)


def test_core_low_score_demotes_only_to_recall(mk):
    weights = _never_accessed_core(mk)
    res = mk.auto_tier("alice", **weights)[0]
    assert res["target_tier"] == "archival"
    assert res["new_tier"] == "recall"
    assert res["demotion_capped"] is True
    assert mk.tier_of("alice") == "recall"


def test_second_run_finishes_demotion(mk):
    weights = _never_accessed_core(mk)
    mk.auto_tier("alice", **weights)
    res = mk.auto_tier("alice", **weights)[0]
    assert res["old_tier"] == "recall" and res["new_tier"] == "archival"
    assert res["demotion_capped"] is False
    assert mk.tier_of("alice") == "archival"


def test_dry_run_reports_cap_without_writing(mk):
    weights = _never_accessed_core(mk)
    res = mk.auto_tier("alice", dry_run=True, **weights)[0]
    assert res["new_tier"] == "recall" and res["demotion_capped"] is True
    assert mk.tier_of("alice") == "core"


def test_promotion_is_not_capped(mk):
    mk.track("Alice")
    mk.tier_set("alice", tier="archival")
    res = mk.auto_tier("alice", core_threshold=0.0, archival_threshold=0.0)[0]
    assert res["target_tier"] == "core" and res["new_tier"] == "core"
    assert res["demotion_capped"] is False
