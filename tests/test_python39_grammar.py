"""Repository-wide grammar compatibility checks for the supported Python floor."""

import ast
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SOURCE_PREFIXES = ("src/", "benchmarks/")


def _tracked_runtime_python_files():
    """Return tracked runtime/benchmark Python files, independent of the cwd."""
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", "src", "benchmarks"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    paths = [Path(item.decode()) for item in output.split(b"\0") if item]
    return sorted(
        path
        for path in paths
        if path.suffix == ".py" and path.as_posix().startswith(RUNTIME_SOURCE_PREFIXES)
    )


def test_tracked_runtime_sources_use_python39_grammar():
    """All shipped runtime and benchmark sources must parse as Python 3.9."""
    paths = _tracked_runtime_python_files()
    assert paths, "git did not report any tracked runtime Python sources"

    failures = []
    for relative_path in paths:
        source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(relative_path), feature_version=(3, 9))
        except SyntaxError as error:
            failures.append(f"{relative_path}:{error.lineno}:{error.offset}: {error.msg}")
            continue

        annotations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.arg) and node.annotation is not None:
                annotations.append(node.annotation)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
                annotations.append(node.returns)
            elif isinstance(node, ast.AnnAssign):
                annotations.append(node.annotation)

        for annotation in annotations:
            for node in ast.walk(annotation):
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                    failures.append(
                        f"{relative_path}:{node.lineno}:{node.col_offset + 1}: "
                        "PEP 604 union annotation requires Python 3.10"
                    )

    assert not failures, "Python 3.9 grammar errors:\n" + "\n".join(failures)
