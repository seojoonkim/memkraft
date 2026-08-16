"""Explicit, artifact-pinned MemKraft self-update support."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_installed_version
from pathlib import Path
from typing import Dict, Iterator, Optional

from .install_integrity import _site_dirs, installation_report

PYPI_URL = "https://pypi.org/pypi/memkraft/json"
PYPI_RELEASE_URL = "https://pypi.org/pypi/memkraft/{}/json"
PYPI_TIMEOUT_SECONDS = 5
_PUBLIC_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactError(RuntimeError):
    """The official release did not provide one safe, verifiable artifact."""


def latest_version(timeout: int = PYPI_TIMEOUT_SECONDS) -> Optional[str]:
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=timeout) as response:
            payload = json.load(response)
        candidates = []
        value = payload.get("info", {}).get("version")
        if value:
            candidates.append(str(value))
        releases = payload.get("releases", {})
        if isinstance(releases, dict):
            candidates.extend(
                str(version)
                for version, files in releases.items()
                if files and _PUBLIC_VERSION.fullmatch(str(version))
            )
        public = [version for version in candidates if _PUBLIC_VERSION.fullmatch(version)]
        return max(public, key=_parse_version) if public else None
    except Exception:
        return None


def installed_version() -> Optional[str]:
    try:
        return get_installed_version("memkraft")
    except PackageNotFoundError:
        return None


def _parse_version(value: str) -> tuple[int, int, int]:
    """Parse the intentionally narrow public X.Y.Z contract used by this command."""
    match = _PUBLIC_VERSION.fullmatch(value or "")
    if not match:
        raise ValueError("unsupported public version {!r}; expected X.Y.Z".format(value))
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def needs_update(current: str, latest: str) -> bool:
    if not current or not latest:
        return False
    return _parse_version(latest) > _parse_version(current)


def release_artifact(version: str, timeout: int = PYPI_TIMEOUT_SECONDS) -> Dict[str, str]:
    """Query official release JSON and select exactly one universal wheel."""
    _parse_version(version)
    try:
        with urllib.request.urlopen(PYPI_RELEASE_URL.format(version), timeout=timeout) as response:
            payload = json.load(response)
    except Exception as exc:
        raise ArtifactError("could not query official PyPI release: {}".format(exc)) from exc
    if str(payload.get("info", {}).get("version") or "") != version:
        raise ArtifactError("PyPI release version mismatch")
    candidates = []
    for item in payload.get("urls", []):
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "")
        if (item.get("packagetype") == "bdist_wheel" and
                item.get("python_version") == "py3" and
                filename.endswith("-py3-none-any.whl")):
            candidates.append(item)
    if len(candidates) != 1:
        raise ArtifactError("expected exactly one py3-none-any wheel; found {}".format(len(candidates)))
    item = candidates[0]
    url = str(item.get("url") or "")
    parsed = urllib.parse.urlparse(url)
    digest = str((item.get("digests") or {}).get("sha256") or "").lower()
    filename = str(item.get("filename") or "")
    if parsed.scheme != "https" or parsed.hostname != "files.pythonhosted.org" or not parsed.path.endswith("/" + filename):
        raise ArtifactError("wheel URL is not an official files.pythonhosted.org artifact")
    if not _SHA256.fullmatch(digest):
        raise ArtifactError("wheel has no valid SHA256 digest")
    return {"filename": filename, "url": url, "sha256": digest}


@contextmanager
def download_verified_wheel(artifact: Dict[str, str], timeout: int = PYPI_TIMEOUT_SECONDS) -> Iterator[str]:
    """Download to a private temporary file, verify SHA256, and always remove it."""
    filename = str(artifact.get("filename") or "")
    if Path(filename).name != filename or not filename.endswith(".whl"):
        raise ArtifactError("wheel has no safe canonical filename")
    directory = tempfile.mkdtemp(prefix="memkraft-update-")
    path = str(Path(directory) / filename)
    try:
        digest = hashlib.sha256()
        with open(path, "xb") as output:
            with urllib.request.urlopen(artifact["url"], timeout=timeout) as response:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest() != artifact["sha256"]:
            raise ArtifactError("downloaded wheel SHA256 mismatch")
        yield path
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _verify_fresh(expected: str) -> subprocess.CompletedProcess:
    code = (
        "import json; from memkraft.install_integrity import installation_report; "
        "r=installation_report(); print(json.dumps(r, sort_keys=True)); "
        "raise SystemExit(0 if r.get('consistent') and r.get('package_version') == {!r} else 1)"
    ).format(expected)
    return subprocess.run([sys.executable, "-c", code], text=True, capture_output=True)


def _quarantine_editable_artifacts(
    report: Dict[str, object],
) -> tuple[str, list[tuple[Path, Path]]]:
    """Atomically move owned editable artifacts aside on their filesystem."""
    redirects = report.get("editable_redirects")
    if not isinstance(redirects, list) or not redirects:
        return "", []
    active_sites = {path.resolve() for path in _site_dirs()}
    prepared: list[tuple[Path, os.stat_result]] = []
    parent: Optional[Path] = None
    for redirect in redirects:
        if not isinstance(redirect, dict):
            raise ArtifactError("invalid editable artifact provenance")
        source = Path(str(redirect.get("artifact") or ""))
        source_parent = source.parent.resolve()
        if source_parent not in active_sites:
            raise ArtifactError("editable artifact is outside an active site directory")
        kind = str(redirect.get("kind") or "")
        owned_name = (
            source.name == "memkraft.egg-link" and kind == "egg-link"
        ) or (
            source.name.startswith("__editable__.memkraft-")
            and source.name.endswith(".pth")
            and kind in {"pep660-pth", "pep660-import-hook"}
        )
        if not owned_name:
            raise ArtifactError("editable artifact is not canonically owned by MemKraft")
        parent_info = source_parent.stat()
        current_uid = os.geteuid() if hasattr(os, "geteuid") else parent_info.st_uid
        if parent_info.st_uid != current_uid:
            raise ArtifactError("active site directory is not owned by the current user")
        info = source.lstat()
        if info.st_uid != current_uid or not stat.S_ISREG(info.st_mode) or source.is_symlink():
            raise ArtifactError("editable artifact is not an owned regular file")
        if parent is not None and source_parent != parent:
            raise ArtifactError("editable artifacts span multiple active site directories")
        parent = source_parent
        prepared.append((source, info))
    assert parent is not None
    backup_dir = Path(tempfile.mkdtemp(prefix=".memkraft-editable-backup-", dir=str(parent)))
    os.chmod(backup_dir, 0o700)
    moved: list[tuple[Path, Path]] = []
    try:
        for index, (source, expected) in enumerate(prepared):
            current = source.lstat()
            if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
                raise ArtifactError("editable artifact changed during convergence")
            destination = backup_dir / "{}-{}".format(index, source.name)
            os.replace(source, destination)
            moved.append((source, destination))
        return str(backup_dir), moved
    except BaseException:
        _restore_quarantine(str(backup_dir), moved)
        raise


def _restore_quarantine(backup_dir: str, moved: list[tuple[Path, Path]]) -> None:
    errors: list[str] = []
    for source, destination in reversed(moved):
        if destination.exists():
            try:
                os.replace(destination, source)
            except OSError as exc:
                errors.append("{}: {}".format(source, exc))
    if errors:
        raise ArtifactError(
            "quarantine rollback incomplete; recovery files preserved at {}: {}".format(
                backup_dir, "; ".join(errors)
            )
        )
    if backup_dir:
        shutil.rmtree(backup_dir, ignore_errors=True)


def _discard_quarantine(backup_dir: str) -> None:
    if backup_dir:
        shutil.rmtree(backup_dir, ignore_errors=True)


def selfupdate(dry_run: bool = False, converge: bool = False, yes: bool = False) -> int:
    latest = latest_version()
    if latest is None:
        print("⚠️  Could not reach PyPI (offline or timeout).")
        return 1
    try:
        _parse_version(latest)
    except ValueError as exc:
        print("❌ Invalid PyPI version: {}".format(exc))
        return 1

    report = installation_report()
    current = report.get("package_version") or installed_version()
    if not isinstance(current, str):
        print("⚠️  MemKraft does not appear to be installed via pip.")
        return 1
    try:
        updating = needs_update(current, latest)
    except ValueError as exc:
        print("❌ Cannot safely compare versions: {}".format(exc))
        return 1
    split = not bool(report.get("consistent"))
    if split and not converge:
        raw_reasons = report.get("reasons")
        reasons = raw_reasons if isinstance(raw_reasons, list) else ["unknown drift"]
        print("❌ Installation is split: {}. Run `memkraft selfupdate --converge`.".format(", ".join(str(item) for item in reasons)))
        return 1
    if not split and not updating and not converge:
        print("✅ Already up to date: {}".format(current))
        return 0

    requirement = "memkraft=={}".format(latest)
    print("🔄 Converge installation to {} from verified PyPI wheel".format(requirement) if converge
          else "🔄 Update available: {} → {}".format(current, latest))
    if dry_run:
        print("(dry-run: would fetch, verify, and force-reinstall the exact {} wheel)".format(requirement))
        return 0
    if converge and not yes:
        try:
            confirmed = input("Force-reinstall {}? [y/N] ".format(requirement)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            confirmed = ""
        if confirmed not in ("y", "yes"):
            print("⚠️  Aborted.")
            return 1

    backup_dir = ""
    moved: list[tuple[Path, Path]] = []
    committed = False
    try:
        artifact = release_artifact(latest)
        with download_verified_wheel(artifact) as wheel:
            if converge:
                backup_dir, moved = _quarantine_editable_artifacts(report)
            command = [sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", wheel]
            print("Running pip against verified local wheel: {}".format(artifact["filename"]))
            result = subprocess.run(command)
            if result.returncode != 0:
                print("❌ pip install failed (exit {})".format(result.returncode))
                return result.returncode
            verified = _verify_fresh(latest)
            if verified.returncode != 0:
                detail = (verified.stdout or verified.stderr).strip() or "fresh interpreter reported drift"
                print("❌ Post-install verification failed: {}".format(detail))
                return 1
            committed = True
    except (ArtifactError, OSError, ValueError, KeyError) as exc:
        print("❌ Convergence failed: {}".format(exc))
        return 1
    finally:
        if backup_dir:
            if committed:
                _discard_quarantine(backup_dir)
            else:
                _restore_quarantine(backup_dir, moved)
    print("✅ On-disk installation verified at {}. Restart active MemKraft/Hermes processes to use it.".format(latest))
    return 0


def cmd(args) -> int:
    return selfupdate(dry_run=getattr(args, "dry_run", False), converge=getattr(args, "converge", False), yes=getattr(args, "yes", False))


def main(argv: Optional[list] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="memkraft selfupdate", description="Self-upgrade MemKraft via pip")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--converge", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    return selfupdate(args.dry_run, args.converge, args.yes)


if __name__ == "__main__":
    sys.exit(main())
