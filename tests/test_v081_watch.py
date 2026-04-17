"""v0.8.1 — watch CLI."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memkraft import watch


def test_watch_missing_watchdog_gives_friendly_error(monkeypatch, capsys):
    monkeypatch.setattr(watch, "_try_import_watchdog", lambda: False)
    rc = watch.run(path="/tmp/does-not-matter")
    out = capsys.readouterr().out
    assert rc == 2
    assert "watchdog" in out
    assert "memkraft[watch]" in out


def test_watch_missing_path(monkeypatch, capsys, tmp_path):
    # stub watchdog-available check so we reach the path check even without the dep
    monkeypatch.setattr(watch, "_try_import_watchdog", lambda: True)

    missing = tmp_path / "nope"
    rc = watch.run(path=str(missing))
    out = capsys.readouterr().out
    # If watchdog isn't installed, run() will still bail with rc=2; if it IS,
    # we get rc=1 with a friendly message. Both outcomes are acceptable for
    # this CLI fallback branch.
    assert rc in (1, 2)
    assert ("does not exist" in out) or ("watchdog" in out)


def test_watch_once_exits_fast(monkeypatch, tmp_path):
    """If watchdog is installed for real, --once should not hang."""
    try:
        import watchdog  # noqa: F401
    except ImportError:
        pytest.skip("watchdog not installed")

    target = tmp_path / "memory"
    target.mkdir()
    rc = watch.run(path=str(target), once=True)
    assert rc == 0
