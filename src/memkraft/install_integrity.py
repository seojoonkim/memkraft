"""Fail-closed, read-only inspection of the active MemKraft installation."""
from __future__ import annotations

import json
import os
import site
import stat
import sys
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Dict, List, Optional

PYPI_URL = "https://pypi.org/pypi/memkraft/json"


def _inside(path: Optional[str], root: Optional[str]) -> bool:
    if not path or not root:
        return False
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def _site_dirs() -> List[Path]:
    """Return only active sys.path entries that are configured site directories."""
    configured: List[str] = []
    try:
        configured.extend(site.getsitepackages())
    except Exception:
        pass
    try:
        configured.append(site.getusersitepackages())
    except Exception:
        pass
    active = set()
    for value in sys.path:
        if value:
            try:
                active.add(Path(value).resolve())
            except OSError:
                continue
    result = []
    for value in configured:
        try:
            resolved = Path(value).resolve()
        except OSError:
            continue
        if resolved in active and resolved not in result:
            result.append(resolved)
    return result


def _editable_redirects(errors: List[str]) -> List[Dict[str, str]]:
    redirects: List[Dict[str, str]] = []
    for directory in _site_dirs():
        try:
            artifacts = list(directory.glob("memkraft.egg-link"))
            artifacts += list(directory.glob("__editable__.memkraft-*.pth"))
            for artifact in artifacts:
                try:
                    info = artifact.lstat()
                    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                        errors.append("editable {}: non-regular or symlink artifact".format(artifact))
                        continue
                    lines = artifact.read_text(encoding="utf-8").splitlines()
                    executable = any(line.lstrip().startswith("import ") for line in lines)
                    targets = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith(("#", "import "))]
                    kind = "egg-link" if artifact.name.endswith(".egg-link") else (
                        "pep660-import-hook" if executable else "pep660-pth"
                    )
                    redirects.append({"artifact": str(artifact), "kind": kind, "target": targets[0] if targets else ""})
                except Exception as exc:
                    errors.append("editable {}: {}".format(artifact, exc))
        except Exception as exc:
            errors.append("site {}: {}".format(directory, exc))
    return redirects


def _public_version(errors: List[str], timeout: int = 5) -> Optional[str]:
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=timeout) as response:
            value = json.load(response).get("info", {}).get("version")
            if not value:
                raise ValueError("missing PyPI version")
            return str(value)
    except Exception as exc:
        errors.append("pypi: {}".format(exc))
        return None


def _metadata_path(distribution) -> Path:
    path = getattr(distribution, "_path", None)
    if path is None:
        raise ValueError("distribution metadata has no physical path")
    return Path(path).resolve()


def collect_probe(*, package_version: str, import_path: str, check_updates: bool = False) -> Dict[str, object]:
    errors: List[str] = []
    matches = []
    provenance: List[str] = []
    seen = set()
    try:
        # Passing active paths prevents importlib.metadata from scanning unrelated
        # environment metadata; canonical physical paths collapse duplicate objects.
        for directory in _site_dirs():
            for distribution in metadata.distributions(path=[str(directory)]):
                name = str(distribution.metadata["Name"] or "").lower().replace("_", "-")
                if name != "memkraft":
                    continue
                try:
                    physical = _metadata_path(distribution)
                except Exception as exc:
                    errors.append("metadata provenance: {}".format(exc))
                    continue
                key = os.path.normcase(str(physical))
                if key not in seen:
                    seen.add(key)
                    matches.append(distribution)
                    provenance.append(str(physical))
    except Exception as exc:
        errors.append("metadata: {}".format(exc))

    distribution_version = None
    distribution_location = None
    if matches:
        try:
            distribution_version = matches[0].version
            distribution_location = str(matches[0].locate_file("").resolve())
        except Exception as exc:
            errors.append("metadata fields: {}".format(exc))
    return {
        "package_version": package_version,
        "import_path": str(Path(import_path).resolve()),
        "distribution_version": distribution_version,
        "distribution_location": distribution_location,
        "distribution_count": len(matches),
        "distribution_provenance": provenance,
        "editable_redirects": _editable_redirects(errors),
        "public_version": _public_version(errors) if check_updates else None,
        "public_check_requested": check_updates,
        "errors": errors,
    }


def evaluate_probe(probe: Dict[str, object]) -> Dict[str, object]:
    reasons: List[str] = []
    package = probe.get("package_version")
    distribution = probe.get("distribution_version")
    count = probe.get("distribution_count")
    import_path = str(probe.get("import_path") or "")
    if probe.get("errors"):
        reasons.append("collection_failure")
    if not distribution or count == 0:
        reasons.append("missing_distribution")
    if package and distribution and package != distribution:
        reasons.append("version_mismatch")
    if isinstance(count, int) and count > 1:
        reasons.append("duplicate_distributions")
    if distribution and not _inside(import_path, str(probe.get("distribution_location") or "")):
        reasons.append("import_outside_distribution")
    if probe.get("editable_redirects"):
        reasons.append("editable_redirect")
    try:
        resolved = str(Path(import_path).resolve())
        if resolved == "/tmp" or resolved.startswith("/tmp/") or resolved == "/private/tmp" or resolved.startswith("/private/tmp/"):
            reasons.append("ephemeral_import")
    except OSError:
        reasons.append("collection_failure")
    if probe.get("public_check_requested") and probe.get("public_version") and package != probe.get("public_version"):
        reasons.append("public_version_drift")
    result = dict(probe)
    result.update({"consistent": not reasons, "reasons": list(dict.fromkeys(reasons))})
    return result


def installation_report(check_updates: bool = False) -> Dict[str, object]:
    from . import __version__
    import memkraft
    return evaluate_probe(collect_probe(package_version=__version__, import_path=str(Path(memkraft.__file__ or "")), check_updates=check_updates))


__all__ = ["collect_probe", "evaluate_probe", "installation_report"]
