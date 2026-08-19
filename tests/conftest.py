"""Make repository tests import the checkout under test, never an installed wheel."""
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def pytest_sessionstart(session):
    """Fail early if pytest resolved MemKraft outside this checkout."""
    import memkraft

    resolved = Path(memkraft.__file__).resolve()
    expected = (SOURCE_ROOT / "memkraft").resolve()
    if expected not in resolved.parents:
        raise RuntimeError(
            "MemKraft tests imported an external installation: "
            f"{resolved}; expected a module below {expected}"
        )

    session.config.stash["memkraft_source_path"] = str(resolved)
