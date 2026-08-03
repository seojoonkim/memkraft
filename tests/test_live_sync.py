"""v3.2 — local-first live sync: invalidation, change events, freshness, repair."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memkraft import MemKraft, _corpus_index, embedding, live_sync, watch  # noqa: E402


FAKE_MODEL = "live-sync-test-fake"


def _fake_vec(text: str, dim: int = 16):
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8", "replace")).digest()
    raw = [((digest[i % len(digest)] + i) % 251) / 251.0 for i in range(dim)]
    norm = sum(x * x for x in raw) ** 0.5 or 1.0
    return [x / norm for x in raw]


class _FakeModel:
    def __init__(self):
        self.encoded_texts = 0

    def encode(self, texts, **kwargs):
        single = isinstance(texts, str)
        batch = [texts] if single else list(texts)
        self.encoded_texts += len(batch)
        out = [_fake_vec(t) for t in batch]
        return out[0] if single else out


@pytest.fixture(autouse=True)
def _clean_corpus_index():
    _corpus_index.reset_for_tests()
    yield
    _corpus_index.reset_for_tests()


@pytest.fixture
def mk(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMKRAFT_EMBEDDING_MODEL", raising=False)
    base = tmp_path / "memory"
    instance = MemKraft(base_dir=str(base))
    instance.live_notes_dir.mkdir(parents=True, exist_ok=True)
    return instance


@pytest.fixture
def fake_model(monkeypatch, mk):
    model = _FakeModel()
    monkeypatch.setitem(embedding._MODEL_CACHE, FAKE_MODEL, model)
    mk._embedding_model_name_override = FAKE_MODEL
    return model


def _note(mk, name: str, body: str) -> Path:
    path = mk.live_notes_dir / name
    path.write_text(body, encoding="utf-8")
    return path


def _pending():
    return set(_corpus_index._PENDING_INVALIDATIONS)


# ── 1. path-aware invalidation ───────────────────────────────────
@pytest.mark.parametrize("operation", ["create", "modify"])
def test_apply_invalidates_exact_path(mk, operation):
    note = _note(mk, "a.md", "alpha content")
    _corpus_index.reset_for_tests()

    result = mk.live_sync_apply(note, operation, embeddings=False)

    assert result["invalidated"] == [str(note)]
    assert note in _pending()
    assert _corpus_index.stats()["write_generation"] >= 1


def test_apply_delete_invalidates_path(mk):
    note = _note(mk, "gone.md", "alpha content")
    note.unlink()
    _corpus_index.reset_for_tests()

    result = mk.live_sync_apply(note, "delete", embeddings=False)

    assert result["invalidated"] == [str(note)]
    assert note in _pending()


def test_apply_move_invalidates_both_sides(mk):
    old = _note(mk, "old.md", "alpha content")
    new = mk.live_notes_dir / "new.md"
    old.rename(new)
    _corpus_index.reset_for_tests()

    result = mk.live_sync_apply(new, "move", old_path=old, embeddings=False)

    assert set(result["invalidated"]) == {str(old), str(new)}
    assert {old, new} <= _pending()


def test_move_requires_old_path(mk):
    with pytest.raises(ValueError):
        mk.live_sync_apply(mk.live_notes_dir / "x.md", "move", embeddings=False)


def test_unknown_operation_rejected(mk):
    with pytest.raises(ValueError):
        mk.live_sync_apply(mk.live_notes_dir / "x.md", "sync", embeddings=False)


def test_apply_ignores_derived_state_paths(mk):
    derived = mk.base_dir / ".memkraft" / "live-sync" / "events.jsonl.md"
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived.write_text("not canonical", encoding="utf-8")
    _corpus_index.reset_for_tests()

    result = mk.live_sync_apply(derived, "modify", embeddings=False)

    assert result["skipped"] is True
    assert result["invalidated"] == []
    assert result["event"] is None
    assert not _pending()


# ── watcher: no search ping, roots at target ─────────────────────
def _require_watchdog():
    try:
        import watchdog  # noqa: F401
    except ImportError:  # pragma: no cover - env dependent
        pytest.skip("watchdog not installed")


class _Event:
    def __init__(self, src, dest=None, is_directory=False):
        self.src_path = str(src)
        self.dest_path = str(dest) if dest is not None else ""
        self.is_directory = is_directory


def test_watch_handler_never_calls_search(mk, monkeypatch):
    _require_watchdog()
    note = _note(mk, "watched.md", "alpha content")
    calls = []
    monkeypatch.setattr(
        type(mk), "search", lambda self, *a, **k: calls.append(a) or [], raising=False
    )

    handler = watch._build_handler(mk)
    handler.on_created(_Event(note))
    handler.on_modified(_Event(note))
    handler.on_deleted(_Event(note))
    handler.on_moved(_Event(note, mk.live_notes_dir / "moved.md"))

    assert calls == []
    events = [e["operation"] for e in mk.live_sync_events()]
    assert events == ["create", "modify", "delete", "move"]


def test_watch_handler_ignores_derived_dir(mk):
    _require_watchdog()
    derived = mk.base_dir / ".memkraft" / "embeddings" / "index.jsonl.md"
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived.write_text("derived", encoding="utf-8")

    handler = watch._build_handler(mk)
    handler.on_modified(_Event(derived))
    handler.on_deleted(_Event(derived))

    assert mk.live_sync_events() == []


def test_watch_roots_memkraft_at_target(tmp_path, monkeypatch):
    _require_watchdog()
    target = tmp_path / "elsewhere"
    (target / "live-notes").mkdir(parents=True)
    monkeypatch.setenv("MEMKRAFT_DIR", str(tmp_path / "unrelated-default"))

    seen = {}

    def _factory(base_dir=None, **kwargs):
        seen["base_dir"] = base_dir
        return MemKraft(base_dir=base_dir)

    monkeypatch.setattr(watch, "MemKraft", _factory)
    rc = watch.run(path=str(target), once=True)

    assert rc == 0
    assert Path(seen["base_dir"]) == target


# ── 2. change-event envelope ─────────────────────────────────────
def test_event_schema_and_fingerprint(mk):
    note = _note(mk, "evt.md", "alpha content")

    result = mk.live_sync_apply(note, "create", embeddings=False)
    event = result["event"]

    assert event["schema"] == live_sync.CHANGE_EVENT_SCHEMA
    assert event["operation"] == "create"
    assert event["path"] == str(note)
    assert event["observed_at"].endswith("Z")
    assert event["fingerprint"].startswith("sha256:")
    assert "old_path" not in event

    log = mk.base_dir / ".memkraft" / "live-sync" / "events.jsonl"
    assert log.exists()
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[0]) == event


def test_event_for_delete_has_no_fingerprint(mk):
    note = mk.live_notes_dir / "deleted.md"
    note.write_text("gone soon", encoding="utf-8")
    note.unlink()

    event = mk.live_sync_apply(note, "delete", embeddings=False)["event"]

    assert event["operation"] == "delete"
    assert "fingerprint" not in event


def test_event_for_move_carries_old_path(mk):
    old = _note(mk, "before.md", "alpha content")
    new = mk.live_notes_dir / "after.md"
    old.rename(new)

    event = mk.live_sync_apply(new, "move", old_path=old, embeddings=False)["event"]

    assert event["old_path"] == str(old)
    assert event["path"] == str(new)
    assert event["fingerprint"].startswith("sha256:")


def test_event_log_is_append_only(mk):
    note = _note(mk, "append.md", "alpha")
    mk.live_sync_apply(note, "create", embeddings=False)
    note.write_text("alpha beta", encoding="utf-8")
    mk.live_sync_apply(note, "modify", embeddings=False)

    events = mk.live_sync_events()
    assert [e["operation"] for e in events] == ["create", "modify"]
    assert events[0]["fingerprint"] != events[1]["fingerprint"]
    assert mk.live_sync_events(limit=1) == events[-1:]


def test_event_linked_to_provenance_without_inlining_content(mk):
    note = _note(mk, "prov.md", "alpha content that should not be inlined")

    result = mk.live_sync_apply(note, "create", embeddings=False)
    event_id = result["event"]["event_id"]

    assert result["provenance_record_id"] == event_id
    raw = (mk.base_dir / ".memkraft" / "provenance.jsonl").read_text(encoding="utf-8")
    assert "should not be inlined" not in raw
    record = mk.why(event_id)
    assert record["transform"] == "live_sync.create"
    assert record["kind"] == "change_event"
    assert record["derived_from"][0]["file"] == str(note)


def test_event_log_io_failure_is_non_fatal(mk, monkeypatch):
    note = _note(mk, "io.md", "alpha content")
    _corpus_index.reset_for_tests()

    def _boom(self, event):
        raise OSError("disk full")

    monkeypatch.setattr(type(mk), "_live_sync_append_event", _boom, raising=False)

    result = mk.live_sync_apply(note, "modify", embeddings=False)

    assert result["event"] is None
    assert "disk full" in result["event_error"]
    # Correctness-critical part still happened.
    assert result["invalidated"] == [str(note)]
    assert note in _pending()


# ── 3. freshness / repair ────────────────────────────────────────
def _build_bm25(mk):
    return _corpus_index.get_corpus_index(
        lambda: mk._all_md_files(), mk._search_tokens, mk._safe_read, trust_write_hooks=True
    )


def test_freshness_reports_not_built_before_any_build(mk):
    _note(mk, "f1.md", "alpha content")
    report = mk.live_sync_freshness()

    assert report["bm25"]["state"] == "not_built"
    assert report["canonical_files"] == 1
    assert report["embedding"]["state"] == "absent"
    assert report["consistent"] is False


def test_freshness_fresh_after_build(mk):
    _note(mk, "f1.md", "alpha content")
    _build_bm25(mk)

    report = mk.live_sync_freshness()
    assert report["bm25"]["state"] == "fresh"
    assert report["bm25"]["missing"] == []
    assert report["consistent"] is True


def test_freshness_stale_after_raw_external_modify(mk):
    note = _note(mk, "f1.md", "alpha content")
    _build_bm25(mk)
    note.write_text("alpha content plus a raw external write", encoding="utf-8")

    report = mk.live_sync_freshness()
    assert report["bm25"]["state"] == "stale"
    assert report["bm25"]["stale"] == [str(note)]


def test_freshness_stale_after_raw_external_delete(mk):
    note = _note(mk, "f1.md", "alpha content")
    _note(mk, "f2.md", "beta content")
    _build_bm25(mk)
    note.unlink()

    report = mk.live_sync_freshness()
    assert report["bm25"]["state"] == "stale"
    assert report["bm25"]["orphaned"] == [str(note)]


def test_repair_rebuilds_bm25_from_markdown(mk):
    note = _note(mk, "f1.md", "alpha content")
    _build_bm25(mk)
    note.write_text("gamma content only", encoding="utf-8")
    assert mk.live_sync_freshness()["bm25"]["state"] == "stale"

    out = mk.live_sync_repair(embeddings=False)

    assert out["bm25"]["rebuilt"] is True
    assert out["freshness"]["bm25"]["state"] == "fresh"


def test_derived_state_can_be_deleted_and_rebuilt(mk, fake_model):
    _note(mk, "f1.md", "alpha content")
    _note(mk, "f2.md", "beta content")
    _build_bm25(mk)
    mk.build_embeddings(force=True)
    before = mk.embedding_index_state()["records"]

    # Nuke every derived artefact — markdown alone must be enough.
    import shutil

    shutil.rmtree(mk.base_dir / ".memkraft")
    _corpus_index.reset_for_tests()
    mk._embedding_doc_cache_obj = None
    assert mk.live_sync_freshness()["bm25"]["state"] == "not_built"

    out = mk.live_sync_repair(embeddings=True)

    assert out["freshness"]["bm25"]["state"] == "fresh"
    assert out["freshness"]["embedding"]["state"] == "fresh"
    assert mk.embedding_index_state()["records"] == before


def test_freshness_reports_corrupt_embedding_index(mk, fake_model):
    _note(mk, "f1.md", "alpha content")
    mk.build_embeddings(force=True)
    index = Path(mk.embedding_stats()["index_path"])
    index.write_text("{not json\n", encoding="utf-8")
    mk._embedding_doc_cache_obj = None

    state = mk.embedding_index_state()
    assert state["state"] == "corrupt"
    assert state["invalid_lines"] == 1

    mk.live_sync_repair(embeddings=True)
    assert mk.embedding_index_state()["state"] == "fresh"


def test_freshness_absent_embedding_index_is_not_an_error(mk):
    _note(mk, "f1.md", "alpha content")
    _build_bm25(mk)

    report = mk.live_sync_freshness()
    assert report["embedding"]["state"] == "absent"
    assert report["consistent"] is True


def test_repair_drops_embedding_for_file_that_became_empty(mk, fake_model):
    note = _note(mk, "f1.md", "alpha content")
    mk.build_embeddings(force=True)
    note.write_text("\n", encoding="utf-8")
    assert mk.embedding_index_state()["state"] == "stale"

    out = mk.live_sync_repair(embeddings=True)

    assert out["freshness"]["embedding"]["state"] == "fresh"
    assert mk.embedding_index_state()["records"] == 0


def test_repair_drops_embeddings_when_all_markdown_deleted(mk, fake_model):
    note = _note(mk, "f1.md", "alpha content")
    mk.build_embeddings(force=True)
    note.unlink()

    out = mk.live_sync_repair(embeddings=True)

    assert out["freshness"]["embedding"]["state"] == "fresh"
    assert mk.embedding_index_state()["records"] == 0


def test_repair_preserves_embedding_when_markdown_read_temporarily_fails(
    mk, fake_model, monkeypatch
):
    note = _note(mk, "f1.md", "alpha content")
    mk.build_embeddings(force=True)
    before, _ = mk._embedding_records_from_disk()
    original_read_text = Path.read_text

    def _read_text(path, *args, **kwargs):
        if path == note:
            raise PermissionError("temporary read failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)

    out = mk.build_embeddings(force=True)
    after, _ = mk._embedding_records_from_disk()

    assert out["read_errors"] == [str(note)]
    assert after[str(note)] == before[str(note)]


# ── 4. single-path embedding sync ────────────────────────────────
def test_embedding_sync_create_encodes_only_that_file(mk, fake_model):
    _note(mk, "a.md", "alpha content")
    _note(mk, "b.md", "beta content")
    mk.build_embeddings(force=True)
    fake_model.encoded_texts = 0
    mk._embedding_text_cache_obj = None

    new = _note(mk, "c.md", "gamma content")
    out = mk.embedding_sync_path(new, "create")

    assert fake_model.encoded_texts == 1
    assert out["encoded"] == 1
    assert out["total"] == 3
    assert mk.embedding_index_state()["state"] == "fresh"


def test_embedding_sync_modify_preserves_unrelated_records(mk, fake_model):
    a = _note(mk, "a.md", "alpha content")
    _note(mk, "b.md", "beta content")
    mk.build_embeddings(force=True)
    records_before, _ = mk._embedding_records_from_disk()
    b_before = dict(records_before[str(mk.live_notes_dir / "b.md")])

    a.write_text("alpha content changed", encoding="utf-8")
    mk.embedding_sync_path(a, "modify")

    records_after, _ = mk._embedding_records_from_disk()
    assert records_after[str(mk.live_notes_dir / "b.md")] == b_before
    assert records_after[str(a)]["vec"] != records_before[str(a)]["vec"]


def test_embedding_sync_delete_drops_stale_vector(mk, fake_model):
    a = _note(mk, "a.md", "alpha content")
    _note(mk, "b.md", "beta content")
    mk.build_embeddings(force=True)
    a.unlink()

    out = mk.embedding_sync_path(a, "delete")

    records, _ = mk._embedding_records_from_disk()
    assert out["removed"] == 1
    assert str(a) not in records
    assert len(records) == 1


def test_embedding_sync_move_rekeys_record(mk, fake_model):
    old = _note(mk, "old.md", "alpha content")
    mk.build_embeddings(force=True)
    new = mk.live_notes_dir / "new.md"
    old.rename(new)

    out = mk.embedding_sync_path(new, "move", old_path=old)

    records, _ = mk._embedding_records_from_disk()
    assert out["removed"] == 1 and out["encoded"] == 1
    assert str(old) not in records
    assert str(new) in records
    assert mk.embedding_index_state()["state"] == "fresh"


def test_embedding_sync_empty_file_drops_record(mk, fake_model):
    a = _note(mk, "a.md", "alpha content")
    mk.build_embeddings(force=True)
    a.write_text("   \n", encoding="utf-8")

    out = mk.embedding_sync_path(a, "modify")

    records, _ = mk._embedding_records_from_disk()
    assert out["encoded"] == 0 and out["removed"] == 1
    assert records == {}
    # An empty markdown file is not "missing coverage" — it carries no signal.
    assert mk.embedding_index_state()["state"] == "fresh"


def test_embedding_index_state_flags_model_mismatch(mk, fake_model, monkeypatch):
    _note(mk, "a.md", "alpha content")
    mk.build_embeddings(force=True)
    other = _FakeModel()
    monkeypatch.setitem(embedding._MODEL_CACHE, "other-fake-model", other)
    mk._embedding_model_name_override = "other-fake-model"

    state = mk.embedding_index_state()
    assert state["state"] == "stale"
    assert state["model_mismatch"] == [str(mk.live_notes_dir / "a.md")]


def test_live_sync_auto_never_loads_model_without_index(mk, monkeypatch):
    note = _note(mk, "a.md", "alpha content")

    def _explode(name):
        raise AssertionError(f"model {name} must not be loaded")

    monkeypatch.setattr(embedding, "_load_st_model", _explode)

    result = mk.live_sync_apply(note, "create")  # embeddings="auto"

    assert result["embedding"] is None
    assert not (mk.base_dir / ".memkraft" / "embeddings" / "index.jsonl").exists()


def test_live_sync_auto_syncs_when_index_exists(mk, fake_model):
    a = _note(mk, "a.md", "alpha content")
    mk.build_embeddings(force=True)
    a.write_text("alpha content changed again", encoding="utf-8")
    mk._embedding_text_cache_obj = None
    fake_model.encoded_texts = 0

    result = mk.live_sync_apply(a, "modify")

    assert result["embedding"]["encoded"] == 1
    assert fake_model.encoded_texts == 1


def test_concurrent_embedding_path_writers_preserve_both_updates(mk, fake_model, monkeypatch):
    a = _note(mk, "a.md", "alpha content")
    b = _note(mk, "b.md", "beta content")
    mk.build_embeddings(force=True)
    a.write_text("alpha revised", encoding="utf-8")
    b.write_text("beta revised", encoding="utf-8")

    original_encode = type(mk).embed_batch
    first_entered = threading.Event()
    release_first = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def _slow_encode(self, texts):
        nonlocal calls
        with calls_lock:
            calls += 1
            this_call = calls
        if this_call == 1:
            first_entered.set()
            release_first.wait(timeout=2.0)
        return original_encode(self, texts)

    monkeypatch.setattr(type(mk), "embed_batch", _slow_encode)
    errors = []

    def _sync(path):
        try:
            instance = MemKraft(base_dir=str(mk.base_dir))
            instance._embedding_model_name_override = FAKE_MODEL
            instance.embedding_sync_path(path, "modify")
        except Exception as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    first = threading.Thread(target=_sync, args=(a,))
    second = threading.Thread(target=_sync, args=(b,))
    first.start()
    assert first_entered.wait(timeout=1.0)
    second.start()
    time.sleep(0.05)
    release_first.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert errors == []
    assert not first.is_alive() and not second.is_alive()
    records, _ = mk._embedding_records_from_disk()
    assert records[str(a)]["vec"] == _fake_vec("alpha revised")
    assert records[str(b)]["vec"] == _fake_vec("beta revised")


def test_move_markdown_to_non_markdown_only_removes_old_embedding(mk, fake_model):
    old = _note(mk, "old.md", "alpha content")
    mk.build_embeddings(force=True)
    new = mk.live_notes_dir / "old.txt"
    old.rename(new)
    fake_model.encoded_texts = 0

    result = mk.live_sync_apply(new, "move", old_path=old)

    records, _ = mk._embedding_records_from_disk()
    assert result["embedding"]["removed"] == 1
    assert result["embedding"]["encoded"] == 0
    assert fake_model.encoded_texts == 0
    assert str(old) not in records
    assert str(new) not in records


def test_move_markdown_into_derived_state_only_removes_old_embedding(mk, fake_model):
    old = _note(mk, "old.md", "alpha content")
    mk.build_embeddings(force=True)
    new = mk.base_dir / ".memkraft" / "quarantine" / "old.md"
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    fake_model.encoded_texts = 0

    result = mk.live_sync_apply(new, "move", old_path=old)

    records, _ = mk._embedding_records_from_disk()
    assert result["embedding"]["removed"] == 1
    assert result["embedding"]["encoded"] == 0
    assert fake_model.encoded_texts == 0
    assert str(old) not in records
    assert str(new) not in records


# ── full-rebuild equivalence ─────────────────────────────────────
def test_incremental_bm25_matches_full_rebuild(mk):
    a = _note(mk, "a.md", "alpha content about projects")
    _note(mk, "b.md", "beta content about projects")
    _build_bm25(mk)

    a.write_text("gamma content about pipelines", encoding="utf-8")
    mk.live_sync_apply(a, "modify", embeddings=False)
    incremental = _build_bm25(mk)
    assert _corpus_index.stats()["incremental_updates"] == 1

    _corpus_index.reset_for_tests()
    full = _build_bm25(mk)

    assert incremental.doc_token_freqs == full.doc_token_freqs
    assert incremental.doc_lengths == full.doc_lengths
    assert incremental.token_doc_freq == full.token_doc_freq
    assert incremental.fingerprint == full.fingerprint

    # …and the user-visible search agrees too.
    full_hits = [r["file"] for r in mk.search("gamma", cache=False)]
    _corpus_index.reset_for_tests()
    _build_bm25(mk)
    rebuilt_hits = [r["file"] for r in mk.search("gamma", cache=False)]
    assert full_hits == rebuilt_hits
    assert any("a.md" in f for f in full_hits)


def test_incremental_embedding_matches_full_rebuild(mk, fake_model):
    a = _note(mk, "a.md", "alpha content")
    _note(mk, "b.md", "beta content")
    mk.build_embeddings(force=True)

    a.write_text("alpha content revised", encoding="utf-8")
    mk.embedding_sync_path(a, "modify")
    incremental, _ = mk._embedding_records_from_disk()

    mk.embedding_clear()
    mk.build_embeddings(force=True)
    full, _ = mk._embedding_records_from_disk()

    assert set(incremental) == set(full)
    for path, rec in full.items():
        assert incremental[path]["vec"] == rec["vec"]
        assert incremental[path]["size"] == rec["size"]


# ── 5. benchmark ─────────────────────────────────────────────────
def _load_bench():
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "benchmarks" / "live_sync_bench.py"
    spec = importlib.util.spec_from_file_location("live_sync_bench", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_embedding_sync_resolves_symlinked_corpus_identity(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMKRAFT_EMBEDDING_MODEL", raising=False)
    real_root = tmp_path / "real-memory"
    alias_root = tmp_path / "alias-memory"
    real_root.mkdir()
    alias_root.symlink_to(real_root, target_is_directory=True)
    mk = MemKraft(base_dir=alias_root)
    target = mk.live_notes_dir / "aliased.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("before", encoding="utf-8")
    model = _FakeModel()
    monkeypatch.setitem(embedding._MODEL_CACHE, FAKE_MODEL, model)
    mk._embedding_model_name_override = FAKE_MODEL
    mk.build_embeddings()

    resolved = target.resolve()
    resolved.write_text("after", encoding="utf-8")
    result = mk.embedding_sync_path(resolved, "modify")

    assert result["encoded"] == 1
    records = {
        rec["file"]: rec
        for rec in map(json.loads, mk._embedding_index_path().read_text().splitlines())
    }
    matching = [
        rec for key, rec in records.items() if Path(key).resolve() == resolved
    ]
    assert len(matching) == 1
    assert matching[0]["size"] == len("after")


def test_benchmark_output_shape_and_structural_counts(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMKRAFT_EMBEDDING_MODEL", raising=False)
    mod = _load_bench()
    result = mod.run_benchmark(docs=12, root=tmp_path / "bench")

    assert result["benchmark"] == "live_sync"
    assert result["docs"] == 12
    assert result["bm25"]["full_rebuild"]["files_read"] == 12
    assert result["bm25"]["incremental"]["files_read"] == 1
    assert result["bm25"]["files_read_ratio"] == 12.0
    assert result["embedding"]["full_rebuild"]["files_encoded"] == 12
    assert result["embedding"]["incremental"]["files_encoded"] == 1
    assert result["embedding"]["files_encoded_ratio"] == 12.0
    for section in ("bm25", "embedding"):
        for phase in ("full_rebuild", "incremental"):
            assert result[section][phase]["wall_ms"] >= 0.0
    assert any("wall_ms is indicative only" in note for note in result["limits"])
