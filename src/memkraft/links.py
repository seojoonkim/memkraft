"""Cross-Entity Link Graph + Backlinks — MemKraft v0.8.0

``[[Wiki Link]]`` patterns inside any Markdown file turn into a bidirectional
graph.  The file system is the graph database; there is no DB.

Storage
-------

Backlink index is persisted at ``memory/.memkraft/links/backlinks.json``::

    {
      "Simon": ["memory/decisions/2026-04-10.md", ...],
      "Hashed": [...]
    }

Each entry in the value list is a relative path from ``base_dir``.

Zero dependencies — stdlib only.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")


def _extract_links(text: str) -> List[str]:
    """Return a deduplicated, order-preserving list of wiki-link targets."""
    seen: Set[str] = set()
    out: List[str] = []
    for m in _WIKILINK_RE.finditer(text):
        raw = m.group(1).strip()
        if not raw:
            continue
        # support ``[[Entity|display]]`` — target is before the ``|``
        target = raw.split("|", 1)[0].strip()
        if target and target not in seen:
            seen.add(target)
            out.append(target)
    return out


class LinksMixin:
    """Mixin added to :class:`MemKraft` providing the link_* API."""

    # --- path helpers ------------------------------------------------------

    def _links_dir(self) -> Path:
        p = self.base_dir / ".memkraft" / "links"  # type: ignore[attr-defined]
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _backlinks_file(self) -> Path:
        return self._links_dir() / "backlinks.json"

    def _forward_links_file(self) -> Path:
        return self._links_dir() / "forward.json"

    def _load_backlinks(self) -> Dict[str, List[str]]:
        f = self._backlinks_file()
        if not f.exists():
            return {}
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _load_forward(self) -> Dict[str, List[str]]:
        f = self._forward_links_file()
        if not f.exists():
            return {}
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    # --- scanning ----------------------------------------------------------

    def _iter_memory_files(self, path: Optional[str] = None) -> Iterable[Path]:
        base: Path = self.base_dir  # type: ignore[attr-defined]
        root = base if path is None else Path(path)
        if not root.is_absolute():
            root = base / root
        if root.is_file() and root.suffix == ".md":
            yield root
            return
        if not root.exists():
            return
        for f in root.rglob("*.md"):
            # ignore the internal management tree
            if ".memkraft" in f.parts:
                continue
            yield f

    def link_scan(self, path: Optional[str] = None) -> Dict[str, Any]:
        """Rebuild the link index from disk.

        When ``path`` is ``None`` (default) the whole tree is rescanned.
        When a path is given, only that file or subtree is (re)indexed,
        merged onto the existing index.
        """
        base: Path = self.base_dir  # type: ignore[attr-defined]

        full_rescan = path is None
        backlinks: Dict[str, Set[str]] = defaultdict(set)
        forward: Dict[str, Set[str]] = defaultdict(set)

        if not full_rescan:
            # preserve the existing index and only overwrite entries that
            # belong to files under ``path``.
            for tgt, srcs in self._load_backlinks().items():
                backlinks[tgt] = set(srcs)
            for src, tgts in self._load_forward().items():
                forward[src] = set(tgts)
            # clear entries coming from the rescanned files
            scanned: Set[str] = set()
            for f in self._iter_memory_files(path):
                rel = str(f.relative_to(base))
                scanned.add(rel)
            for src in list(forward):
                if src in scanned:
                    for t in forward[src]:
                        backlinks.get(t, set()).discard(src)
                    del forward[src]

        for f in self._iter_memory_files(path):
            rel = str(f.relative_to(base))
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            targets = _extract_links(text)
            if targets:
                forward[rel] = set(targets)
            for t in targets:
                backlinks[t].add(rel)

        # serialise as sorted lists for deterministic output
        bl_out = {k: sorted(v) for k, v in backlinks.items() if v}
        fw_out = {k: sorted(v) for k, v in forward.items() if v}
        self._save_json(self._backlinks_file(), bl_out)
        self._save_json(self._forward_links_file(), fw_out)
        return {
            "files_scanned": len(fw_out) if full_rescan else None,
            "entities_linked": len(bl_out),
        }

    # --- queries -----------------------------------------------------------

    def link_backlinks(self, entity: str) -> List[str]:
        """Files that mention ``[[entity]]``.  Index is loaded lazily."""
        bl = self._load_backlinks()
        if not bl:
            # first call on a fresh repo — scan so we return something useful
            self.link_scan()
            bl = self._load_backlinks()
        return list(bl.get(entity, []))

    def link_forward(self, source: str) -> List[str]:
        """Entities referenced from a given file (relative path)."""
        fw = self._load_forward()
        if not fw:
            self.link_scan()
            fw = self._load_forward()
        return list(fw.get(source, []))

    def link_graph(
        self,
        entity: str,
        *,
        hops: int = 1,
    ) -> Dict[str, Any]:
        """N-hop link graph around ``entity``.

        Returns ``{"nodes": [...], "edges": [(src, dst), ...]}``.  Edges
        flow in the "source file mentions target entity" direction; each
        hop expands through either (a) targets of files that mention the
        current entity or (b) files that mention any discovered entity.
        """
        if hops < 1:
            raise ValueError("hops must be >= 1")

        bl = self._load_backlinks()
        fw = self._load_forward()
        if not bl and not fw:
            self.link_scan()
            bl = self._load_backlinks()
            fw = self._load_forward()

        nodes: Set[str] = {entity}
        edges: Set[tuple] = set()
        frontier: deque = deque([(entity, 0)])
        seen: Set[str] = {entity}

        while frontier:
            node, depth = frontier.popleft()
            if depth >= hops:
                continue
            # files that mention this entity → their other entities
            for src in bl.get(node, []):
                edges.add((src, node))
                for other in fw.get(src, []):
                    if other != node:
                        edges.add((src, other))
                        if other not in seen:
                            seen.add(other)
                            nodes.add(other)
                            frontier.append((other, depth + 1))
                # also walk the file-as-node
                if src not in seen:
                    seen.add(src)
                    frontier.append((src, depth + 1))

        return {
            "root": entity,
            "hops": hops,
            "nodes": sorted(nodes),
            "edges": sorted(edges),
        }

    def link_orphans(self) -> List[str]:
        """Entities that appear in forward links but have no backlinks at all.

        These are "dangling references" — files reference them via
        ``[[entity]]`` but no file *is* that entity.  We surface them as
        orphans so the user can decide whether to create a stub page.
        """
        bl = self._load_backlinks()
        fw = self._load_forward()
        if not bl and not fw:
            self.link_scan()
            bl = self._load_backlinks()
            fw = self._load_forward()

        # every target mentioned anywhere
        mentioned: Set[str] = set(bl.keys())
        # entity names we *have* files for are stems of files in entities/
        base: Path = self.base_dir  # type: ignore[attr-defined]
        entity_stems: Set[str] = set()
        for sub in ("entities", "live-notes", "facts"):
            d = base / sub
            if d.exists():
                for f in d.glob("*.md"):
                    entity_stems.add(f.stem)
        # an orphan is a mentioned entity whose slugified name doesn't exist
        # as a file, *and* which has at least one inbound mention.
        out: List[str] = []
        for name in sorted(mentioned):
            slug = self._slugify(name)  # type: ignore[attr-defined]
            if slug not in entity_stems and bl.get(name):
                out.append(name)
        return out
