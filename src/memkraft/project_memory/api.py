"""Python Preview API for deterministic local Project Memory compilation."""
import fnmatch
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..context_compiler import estimate_tokens
from .digest import canonical_json, digest_fields
from .errors import ProjectMemoryError, fail
from .model import COMPILER_SCHEMA, validate_project_id
from .normalize import normalize_documents
from .reducer import observations_from_evidence, reduce_observations
from .store import apply_snapshot, read_manifest, validate_owned

_DEFAULT_LIMITS = None


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _slug(value: str) -> str:
    chars = [(ch.lower() if ch.isalnum() else "-") for ch in value]
    slug = "".join(chars).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "project"


def _relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _identity(root: Path, project_id: Optional[str]) -> str:
    if project_id is not None:
        return validate_project_id(project_id)
    suffix = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return validate_project_id("%s-%s" % (_slug(root.name), suffix))


def _config(include: Any, exclude: Any, limits: Any) -> Dict[str, Any]:
    includes = list(include) if include is not None else ["**/*.md", "*.md"]
    excludes = list(exclude) if exclude is not None else []
    if (not all(isinstance(x, str) for x in includes + excludes)
            or any(not x for x in includes + excludes)):
        fail("E_PM_VALIDATION", "include and exclude must contain patterns")
    return {"include": sorted(set(includes)), "exclude": sorted(set(excludes)),
            "limits": limits}


def _scan(root: Path, output_root: Path, config: Dict[str, Any]) -> Tuple[List[Tuple[str, str]], List[Dict[str, Any]]]:
    if not root.exists() or not root.is_dir():
        fail("E_PM_VALIDATION", "project path must be a directory", path=str(root))
    if _relative_to(root, output_root) or _relative_to(output_root, root):
        fail("E_PM_PATH_ESCAPE", "project and output roots must be disjoint")
    files = []
    ledger = []
    try:
        candidates = sorted(root.rglob("*"))
    except OSError as exc:
        fail("E_PM_SOURCE_UNREADABLE", "cannot enumerate project", error=str(exc))
    for candidate in candidates:
        rel_raw = candidate.relative_to(root).as_posix()
        if ".memkraft" in candidate.relative_to(root).parts:
            continue
        if candidate.name in ("AGENTS.md", "CLAUDE.md"):
            continue
        if not any(fnmatch.fnmatch(rel_raw, pattern) for pattern in config["include"]):
            continue
        if any(fnmatch.fnmatch(rel_raw, pattern) for pattern in config["exclude"]):
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            fail("E_PM_SOURCE_UNREADABLE", "cannot resolve source", path=rel_raw, error=str(exc))
        if candidate.is_symlink() and not _relative_to(resolved, root):
            fail("E_PM_PATH_ESCAPE", "symlink resolves outside project", path=rel_raw)
        if candidate.is_symlink():
            continue
        if not candidate.is_file():
            continue
        rel = rel_raw
        try:
            raw = candidate.read_bytes()
            text = raw.decode("utf-8")
            stat = candidate.stat()
        except (OSError, UnicodeError) as exc:
            fail("E_PM_SOURCE_UNREADABLE", "cannot read UTF-8 source", path=rel, error=str(exc))
        files.append((rel, text))
        ledger.append({"path": rel, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                       "content_digest": _sha256(raw)})
    return files, ledger


def _output_root(instance: Any, project_id: str) -> Path:
    return Path(instance.base_dir).resolve() / ".memkraft" / "project-memory" / project_id


class ProjectMemoryMixin:
    def project_build(self, path, *, as_of, project_id=None, include=None,
                      exclude=None, limits=None, dry_run=True) -> dict:
        root = Path(path).resolve()
        identity = _identity(root, project_id)
        output_root = _output_root(self, identity)
        config = _config(include, exclude, limits)
        files, ledger = _scan(root, output_root, config)
        compiled = reduce_observations(normalize_documents(files, limits=limits),
                                       as_of=as_of, config=config)
        manifest = {"compiler_schema": 1, "project_id": identity,
                    "project_root": str(root), "current_snapshot_id": compiled["snapshot_id"],
                    "semantic_digest": compiled["semantic_digest"],
                    "config": config, "inputs": ledger}
        counts = {"observations": len(compiled["observations"]),
                  "sections": len(compiled["sections"])}
        result = {"schema_version": 1, "dry_run": bool(dry_run), "project_id": identity,
                  "output_root": str(output_root), "compiler_schema": 1,
                  "as_of": compiled["as_of"], "snapshot_id": compiled["snapshot_id"],
                  "semantic_digest": compiled["semantic_digest"],
                  "config_digest": compiled["config_digest"],
                  "inputs": {"files": len(files), "bytes": sum(x["size"] for x in ledger),
                             "reused": 0, "recompiled": len(files)},
                  "counts": counts, "status": "planned"}
        if dry_run:
            if output_root.exists():
                validate_owned(output_root, identity)
            return result
        header = {"compiler_schema": 1, "project_id": identity,
                  "snapshot_id": compiled["snapshot_id"], "semantic_digest": compiled["semantic_digest"],
                  "config_digest": compiled["config_digest"], "as_of": compiled["as_of"],
                  "config": config, "counts": counts}
        status = apply_snapshot(output_root, identity, compiled, header, manifest)
        result.update({"dry_run": False, "applied": True, "status": status,
                       "snapshot_path": str(output_root / "snapshots" / compiled["snapshot_id"])})
        if status == "applied":
            self.provenance_record(compiled["snapshot_id"],
                                   [{"file": str(root / row["path"])} for row in ledger],
                                   transform="project-memory-compiler-v0", kind="project_snapshot",
                                   value=compiled["semantic_digest"])
        return result

    def project_update(self, path, *, as_of, project_id=None, dry_run=True) -> dict:
        root = Path(path).resolve()
        identity = _identity(root, project_id)
        manifest = read_manifest(_output_root(self, identity), identity)
        if manifest is None:
            return self.project_build(root, as_of=as_of, project_id=identity, dry_run=dry_run)
        config = manifest["config"]
        return self.project_build(root, as_of=as_of, project_id=identity,
                                  include=config["include"], exclude=config["exclude"],
                                  limits=config["limits"], dry_run=dry_run)

    def project_status(self, path, *, project_id=None) -> dict:
        root = Path(path).resolve()
        identity = _identity(root, project_id)
        output_root = _output_root(self, identity)
        manifest = read_manifest(output_root, identity)
        base = {"schema_version": 1, "project_id": identity, "output_root": str(output_root),
                "initialized": manifest is not None, "compiler_schema": 1,
                "current_snapshot_id": None, "stale": True, "added": [], "changed": [],
                "removed": [], "unchanged": 0, "reason": "never_built"}
        if manifest is None:
            return base
        _, current = _scan(root, output_root, manifest["config"])
        old = {row["path"]: row for row in manifest["inputs"]}
        new = {row["path"]: row for row in current}
        added = sorted(set(new) - set(old)); removed = sorted(set(old) - set(new))
        changed = sorted(path for path in set(old) & set(new)
                         if old[path]["content_digest"] != new[path]["content_digest"])
        unchanged = len(set(old) & set(new) - set(changed))
        reason = ("input_added" if added else "input_removed" if removed else
                  "content_digest_mismatch" if changed else None)
        base.update({"current_snapshot_id": manifest["current_snapshot_id"],
                     "stale": bool(added or removed or changed), "added": added,
                     "removed": removed, "changed": changed, "unchanged": unchanged,
                     "reason": reason})
        return base

    def project_context(self, query, path, *, budget=2000, top_k=20, project_id=None) -> dict:
        if not isinstance(query, str) or not query or not isinstance(budget, int) or budget < 0:
            fail("E_PM_VALIDATION", "query and budget are invalid")
        root = Path(path).resolve(); identity = _identity(root, project_id)
        output_root = _output_root(self, identity); manifest = read_manifest(output_root, identity)
        if manifest is None:
            fail("E_PM_NOT_BUILT", "project has not been built")
        snapshot_id = manifest["current_snapshot_id"]; snapshot = output_root / "snapshots" / snapshot_id
        try:
            evidence_raw = (snapshot / "evidence.jsonl").read_bytes()
            sections_raw = (snapshot / "sections.jsonl").read_bytes()
            evidence = observations_from_evidence(evidence_raw)
            sections = [json.loads(line) for line in sections_raw.decode("utf-8").splitlines()]
            header = json.loads((snapshot / "project.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            fail("E_PM_DIGEST_MISMATCH", "snapshot cannot be read", error=str(exc))
            raise AssertionError("unreachable")
        if not isinstance(header, dict) or not isinstance(header.get("config"), dict):
            fail("E_PM_DIGEST_MISMATCH", "snapshot header is invalid")
        for observation in evidence:
            try:
                locator = observation["locator"]
                content_digest = _sha256(observation["excerpt"].encode("utf-8"))
                observation_id = digest_fields(locator["path"], locator["lines"][0],
                                               locator["lines"][1], content_digest)
            except (KeyError, TypeError, UnicodeError):
                fail("E_PM_DIGEST_MISMATCH", "evidence record is invalid")
                raise AssertionError("unreachable")
            if (observation.get("content_digest") != content_digest
                    or observation.get("observation_id") != observation_id):
                fail("E_PM_DIGEST_MISMATCH", "evidence content digest mismatch")
        rebuilt = reduce_observations(evidence, as_of=header.get("as_of"),
                                      config=header["config"])
        if (header.get("snapshot_id") != snapshot_id
                or rebuilt["snapshot_id"] != snapshot_id
                or rebuilt["semantic_digest"] != manifest.get("semantic_digest")
                or rebuilt["evidence_bytes"] != evidence_raw
                or rebuilt["sections_bytes"] != sections_raw):
            fail("E_PM_DIGEST_MISMATCH", "snapshot integrity check failed")
        by_id = {row["observation_id"]: row for row in evidence}
        terms = [term.casefold() for term in query.split()]
        ranked = []
        for section in sections:
            observation = by_id.get(section["observation_id"])
            if observation is None:
                fail("E_PM_DIGEST_MISMATCH", "section cites missing evidence")
            text = observation["excerpt"]
            score = sum(text.casefold().count(term) for term in terms)
            item = dict(section); item["text"] = text
            ranked.append((-score, section["section_id"], section["observation_id"], item))
        ranked.sort(key=lambda row: row[:3]); candidates = [row[3] for row in ranked[:top_k]]
        selected = []; used = 0
        for item in candidates:
            cost = estimate_tokens(canonical_json(item))
            if used + cost <= budget:
                selected.append(item); used += cost
        sources = sorted({item["locator"]["path"] for item in selected})
        stale = self.project_status(root, project_id=identity)["stale"]
        context_id = _sha256(canonical_json({"query": query, "snapshot_id": snapshot_id,
                                             "budget": budget, "top_k": top_k,
                                             "sections": [x["section_id"] for x in selected]}).encode("utf-8"))
        return {"schema_version": 1, "context_id": context_id, "query": query,
                "project_id": identity, "snapshot_id": snapshot_id, "stale": stale,
                "budget": budget, "estimated_tokens": used,
                "omitted": len(candidates) - len(selected), "sections": selected,
                "sources": sources}
