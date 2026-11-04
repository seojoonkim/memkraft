from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("agent.memory_provider")

from memkraft.hermes_provider import MemKraftMemoryProvider

ADDRESS = "42 Banpo-daero, Seocho-gu, Seoul"


def _provider(tmp_path: Path, monkeypatch) -> MemKraftMemoryProvider:
    monkeypatch.setenv("MEMKRAFT_INSTALL_CHECK", "off")
    monkeypatch.setenv("MEMKRAFT_DIR", str(tmp_path / "mk"))
    monkeypatch.setenv("MEMKRAFT_OWNER_DIR", str(tmp_path / "owner"))
    provider = MemKraftMemoryProvider()
    provider.initialize("s1", hermes_home=str(tmp_path))
    provider._persist_completed_turn("s1", "User: my home address is {}\nAssistant: noted.".format(ADDRESS))
    provider._persist_completed_turn("s1", "User: my favourite tea is jasmine green tea\nAssistant: noted.")
    return provider


def test_forgotten_event_value_does_not_come_back_through_recall(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch)
    provider._store.append_event("simon", "home_address", ADDRESS, source="chat")
    assert "Banpo-daero" in provider.prefetch("home address")

    provider._store.forget({"subject": "simon", "key": "home_address"}, dry_run=False)

    assert "Banpo-daero" not in provider.prefetch("home address")
    assert "jasmine" in provider.prefetch("favourite tea")


def test_do_not_remember_policy_also_hides_quoted_value(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch)
    provider._store.append_event("simon", "home_address", ADDRESS, source="chat")
    provider._store.do_not_remember(subject="simon", key="home_address", dry_run=False)

    assert "Banpo-daero" not in provider.prefetch("home address")


def test_forget_text_hides_free_text_and_stores_no_plaintext(tmp_path, monkeypatch):
    from memkraft import recall_suppress

    provider = _provider(tmp_path, monkeypatch)
    plan = provider._store.forget_text(ADDRESS)
    assert plan["status"] == "planned"
    assert "Banpo-daero" in provider.prefetch("home address")

    assert provider._store.forget_text(ADDRESS, dry_run=False)["status"] == "applied"
    assert provider._store.forget_text(ADDRESS, dry_run=False)["status"] == "already_applied"

    assert "Banpo-daero" not in provider.prefetch("home address")
    assert "jasmine" in provider.prefetch("favourite tea")
    stored = recall_suppress.suppress_path(provider._store.base_dir).read_text(encoding="utf-8")
    assert "banpo" not in stored.lower()


def test_snippet_cut_through_the_value_is_still_caught():
    from memkraft import recall_suppress

    table = {}
    for length, digest in recall_suppress.digests_for(ADDRESS):
        table.setdefault(length, set()).add(digest)
    assert recall_suppress.contains_suppressed("...address is 42 Banpo-dae", table)
    assert recall_suppress.contains_suppressed("ho-gu,  SEOUL and more", table)
    assert not recall_suppress.contains_suppressed("favourite tea is jasmine", table)


def test_short_values_are_rejected_so_recall_is_not_blanked(tmp_path, monkeypatch):
    from memkraft import recall_suppress

    provider = _provider(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        provider._store.forget_text("yes")
    assert recall_suppress.digests_for("42") == []
