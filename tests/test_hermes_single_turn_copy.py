"""Each completed Hermes turn is ranked once, not once per stored copy."""
import pytest

pytest.importorskip("agent.memory_provider")

from memkraft import MemKraft
from memkraft.hermes_provider import MemKraftMemoryProvider, _uncovered_chunks


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("MEMKRAFT_DIR", raising=False)
    monkeypatch.delenv("MEMKRAFT_HERMES_SEARCH_CHUNKS", raising=False)
    monkeypatch.setenv("MEMKRAFT_INSTALL_CHECK", "off")


def _provider(home, session="s"):
    provider = MemKraftMemoryProvider()
    provider.initialize(session, hermes_home=str(home))
    return provider


def _files(provider, query):
    return [hit["file"] for hit in provider._store.search(query, top_k=20, cache=False)]


def test_turn_is_found_through_its_artifact_only(tmp_path):
    provider = _provider(tmp_path)
    provider.sync_turn("The vault token is AmberHeron.", "Recorded.", session_id="s")

    files = _files(provider, "AmberHeron")

    assert any(f.startswith("artifacts/") for f in files)
    assert not any(f.startswith("live-notes/hermes-session-") for f in files)
    assert "AmberHeron" in provider.prefetch("AmberHeron", session_id="s")
    # Both copies are still on disk: only ranking changed.
    assert list((tmp_path / "memkraft" / "live-notes").glob("hermes-session-*.md"))


def test_store_written_before_this_change_keeps_artifactless_chunks(tmp_path):
    first = _provider(tmp_path)
    first.sync_turn("The vault token is AmberHeron.", "Recorded.", session_id="s")
    first.sync_turn("The legacy token is CobaltFinch.", "Recorded.", session_id="old")
    base = tmp_path / "memkraft"
    for path in (base / "artifacts").iterdir():
        if "CobaltFinch" in path.read_text(encoding="utf-8"):
            path.unlink()
    # An older store has chunks and artifacts but no bookkeeping file yet.
    (base / ".memkraft" / "index" / "turn-chunks-without-artifact.json").unlink()

    second = _provider(tmp_path)

    assert any(f.startswith("live-notes/hermes-session-") for f in _files(second, "CobaltFinch"))
    assert not any(f.startswith("live-notes/hermes-session-") for f in _files(second, "AmberHeron"))


def test_failed_artifact_write_keeps_the_chunk_searchable(tmp_path, monkeypatch):
    provider = _provider(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(provider._store, "persist_artifact", boom)
    with pytest.raises(OSError):
        provider.sync_turn("The orphan token is JadeOtter.", "Recorded.", session_id="s")

    assert any(f.startswith("live-notes/hermes-session-") for f in _files(provider, "JadeOtter"))
    restarted = _provider(tmp_path)
    assert any(f.startswith("live-notes/hermes-session-") for f in _files(restarted, "JadeOtter"))


def test_turn_split_across_chunks_counts_as_covered(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_HERMES_TURN_FILE_BYTES", "512")
    provider = _provider(tmp_path)
    provider.sync_turn("Split token TealIbis " + "x" * 900, "Recorded.", session_id="s")
    base = tmp_path / "memkraft"

    assert len(list((base / "live-notes").glob("hermes-session-*.md"))) >= 2
    assert _uncovered_chunks(base / "live-notes", base / "artifacts") == set()


def test_other_live_notes_are_never_excluded(tmp_path):
    provider = _provider(tmp_path)
    note = tmp_path / "memkraft" / "live-notes" / "ops.md"
    note.write_text("# Ops\n\nThe runbook token is OchreLynx.\n", encoding="utf-8")

    assert "live-notes/ops.md" in _files(provider, "OchreLynx")


def test_env_switch_restores_searching_both_copies(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_HERMES_SEARCH_CHUNKS", "1")
    provider = _provider(tmp_path)
    provider.sync_turn("The vault token is AmberHeron.", "Recorded.", session_id="s")

    files = _files(provider, "AmberHeron")

    assert any(f.startswith("live-notes/hermes-session-") for f in files)
    assert any(f.startswith("artifacts/") for f in files)


def test_plain_store_without_provider_searches_everything(tmp_path):
    provider = _provider(tmp_path)
    provider.sync_turn("The vault token is AmberHeron.", "Recorded.", session_id="s")

    plain = MemKraft(base_dir=str(tmp_path / "memkraft"))

    files = [hit["file"] for hit in plain.search("AmberHeron", top_k=20, cache=False)]
    assert any(f.startswith("live-notes/hermes-session-") for f in files)


def test_filtered_and_plain_search_keep_separate_index_caches(tmp_path):
    provider = _provider(tmp_path)
    provider.sync_turn("The vault token is AmberHeron.", "Recorded.", session_id="s")
    plain = MemKraft(base_dir=str(tmp_path / "memkraft"))

    assert provider._store._corpus_token_cache_path() != plain._corpus_token_cache_path()
    # Alternating searches in one process must not serve the other's file set.
    for _ in range(2):
        assert not any(f.startswith("live-notes/hermes-session-") for f in _files(provider, "AmberHeron"))
        hits = plain.search("AmberHeron", top_k=20, cache=False)
        assert any(h["file"].startswith("live-notes/hermes-session-") for h in hits)
