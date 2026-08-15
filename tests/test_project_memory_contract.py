import ast
import importlib
from pathlib import Path

import pytest


def test_framed_digest_distinguishes_absent_empty_and_boundaries():
    from memkraft.project_memory.digest import digest_fields
    assert len({digest_fields(None), digest_fields(""), digest_fields("a", "bc"), digest_fields("ab", "c")}) == 4


def test_as_of_validation():
    from memkraft.project_memory.model import normalize_as_of
    from memkraft.project_memory.errors import ProjectMemoryError
    assert normalize_as_of("2026-08-15T01:00:00+01:00") == "2026-08-15T00:00:00Z"
    for bad in ("2026-08-15T00:00:00", "2026-08-15T00:00:60Z", "2026-08-15T00:00:00-00:00"):
        with pytest.raises(ProjectMemoryError, match="timestamp") as exc:
            normalize_as_of(bad)
        assert exc.value.code == "E_PM_VALIDATION"


def test_pure_modules_have_no_effectful_imports():
    root = Path(__file__).parents[1] / "src/memkraft/project_memory"
    banned = {"time", "datetime", "random", "os", "subprocess", "socket"}
    for name in ("digest.py", "model.py", "normalize.py", "reducer.py"):
        tree = ast.parse((root / name).read_text())
        imports = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
        imports |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not imports & banned


def test_error_registry_has_nine_stable_codes():
    from memkraft.project_memory.errors import PROJECT_MEMORY_ERROR_REGISTRY
    assert len(PROJECT_MEMORY_ERROR_REGISTRY) == 9
    assert PROJECT_MEMORY_ERROR_REGISTRY["E_PM_STORE_BUSY"] == ("io", True)
