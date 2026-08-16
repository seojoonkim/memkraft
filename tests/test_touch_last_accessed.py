from pathlib import Path

from memkraft import MemKraft
from memkraft import _core_lifecycle_helpers as lifecycle_helpers


def test_throttled_touch_skips_file_stats(tmp_path, monkeypatch):
    note = tmp_path / "entities" / "alice.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Alice\n", encoding="utf-8")
    mk = MemKraft(base_dir=tmp_path)

    monkeypatch.setattr(lifecycle_helpers, "touch_last_accessed", lambda *args: False)
    stat_calls = []
    original_stat = Path.stat

    def counted_stat(path, *args, **kwargs):
        if path == note:
            stat_calls.append(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counted_stat)
    mk._touch_last_accessed("entities/alice.md", "2026-08-16 12:00:00")

    assert stat_calls == []


def test_written_touch_invalidates_read_cache(tmp_path, monkeypatch):
    note = tmp_path / "entities" / "alice.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Alice\n", encoding="utf-8")
    mk = MemKraft(base_dir=tmp_path)

    monkeypatch.setattr(lifecycle_helpers, "touch_last_accessed", lambda *args: True)
    invalidated = []

    class FakeCache:
        def invalidate(self, path):
            invalidated.append(path)

    monkeypatch.setattr("memkraft._read_cache.get_cache", lambda: FakeCache())
    mk._touch_last_accessed("entities/alice.md", "2026-08-16 12:00:00")

    assert invalidated == [note]


def test_touch_invalidates_cache_when_write_changes_file_then_raises(tmp_path, monkeypatch):
    note = tmp_path / "entities" / "alice.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Alice\n**Last Accessed:** old\n", encoding="utf-8")
    mk = MemKraft(base_dir=tmp_path)
    invalidated = []

    class FakeCache:
        def invalidate(self, path):
            invalidated.append(path)

    original_write_text = Path.write_text

    def write_then_raise(path, content, *args, **kwargs):
        result = original_write_text(path, content, *args, **kwargs)
        if path == note:
            raise OSError("simulated close failure after write")
        return result

    monkeypatch.setattr(Path, "write_text", write_then_raise)
    monkeypatch.setattr("memkraft._read_cache.get_cache", lambda: FakeCache())

    mk._touch_last_accessed("entities/alice.md", "2026-08-16 12:00:00")

    assert "2026-08-16 12:00:00" in note.read_text(encoding="utf-8")
    assert invalidated == [note]
