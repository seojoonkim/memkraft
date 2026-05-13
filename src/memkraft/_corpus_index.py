"""Singleton BM25 corpus index cache (v2.7.5).

The legacy ``MemKraft.search()`` method recomputed per-document term
frequencies, document lengths, and global document frequency on every
call.  For a 1k-doc corpus that's hundreds of file reads + tokenizations
per query — the dominant cost in the WS-A benchmarks.

This module caches the entire BM25 index (DF, TF, doc lengths) keyed
by a fingerprint over ``(path, mtime_ns, size)`` of every markdown
file in the corpus.  When any file is added, removed, or modified the
fingerprint changes and the index is rebuilt; otherwise repeated
searches reuse the cached index instantly.

Internal only.  Public ``MemKraft.search()`` and ``search_v2`` /
``search_smart`` benefit transparently.

Configuration
-------------
* ``MEMKRAFT_CORPUS_INDEX_DISABLE=1`` — disable the cache and rebuild
  on every call (regression-test escape hatch).

Thread safety
-------------
Singleton with a module-level lock around build + read.  The cached
:class:`CorpusIndex` is treated as read-only by callers; mutating any
of its dict / list fields is undefined behaviour and would corrupt
subsequent search calls.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

log = logging.getLogger("memkraft._corpus_index")

_ENV_DISABLE = "MEMKRAFT_CORPUS_INDEX_DISABLE"


def _disabled() -> bool:
    raw = os.environ.get(_ENV_DISABLE, "")
    return raw.strip() not in ("", "0", "false", "False", "FALSE")


@dataclass
class CorpusIndex:
    """Cached BM25 corpus statistics.

    Attributes are conceptually read-only; mutation is undefined.
    """

    doc_count: int
    avg_doc_len: float
    token_doc_freq: Dict[str, int] = field(default_factory=dict)
    doc_token_freqs: Dict[Path, Dict[str, int]] = field(default_factory=dict)
    doc_lengths: Dict[Path, int] = field(default_factory=dict)
    fingerprint: bytes = b""
    # v2.8.1: cheap identity hash (paths only, no stat) so the fast
    # path can verify it's looking at the right corpus when multiple
    # MemKraft instances share the singleton (e.g. across test runs).
    path_set_hash: bytes = b""
    # v2.8.3: per-doc (mtime_ns, size) so the search prefilter can
    # detect raw-write bypass (writes that didn't go through MemKraft
    # invalidate hooks) and refuse to skip a stale doc.
    doc_stat: Dict[Path, tuple] = field(default_factory=dict)
    # v2.8.4: cached ordered file list — callers (notably
    # ``MemKraft.search``) used to invoke ``self._all_md_files()`` a
    # second time after building the index, which on a 1k-doc corpus
    # repeats the entire directory scan + stat dance.  We now snapshot
    # the list at build time (deterministic order, same as
    # ``_compute_fingerprint``) so search loops can reuse it.
    file_list: list = field(default_factory=list)
    # v2.9 — inverted posting list (P3 in v2.9.0; compressed in v2.9.1).
    # ``content_postings[token]`` → list of **doc_ids** (ints) whose
    # body has that token (built from ``doc_token_freqs`` — same
    # source, just transposed).  ``filename_postings[token]`` → list
    # of **doc_ids** whose filename (stem, hyphens→spaces, lowered)
    # contains that token.  Together they let ``search()`` skip the
    # per-doc prefilter scan and iterate only candidate docs.
    #
    # v2.9.1 (P2): postings store int doc_ids instead of ``Path``
    # objects to cut memory ~40-60% on large corpora (Path objects
    # are ~200-300 bytes each; small ints share interned slots).
    # ``doc_id_map[doc_id] -> Path`` reverses the index when search
    # needs the actual path.  Public API unchanged — callers fetch
    # Paths via ``doc_id_map`` indexing.
    #
    # Filename substring matches (query in filename_lower) still need
    # the brute filename scan because token lookup misses
    # substrings of long filenames; the per-doc cost is one
    # ``str.__contains__`` though, much cheaper than the prefilter.
    content_postings: Dict[str, list] = field(default_factory=dict)
    filename_postings: Dict[str, list] = field(default_factory=dict)
    # v2.9.1 — doc_id <-> Path mapping.  ``doc_id_map[i]`` is the
    # Path corresponding to doc_id ``i``.  Stable for the lifetime of
    # this index instance (rebuilt on every corpus change anyway).
    doc_id_map: list = field(default_factory=list)
    # Reverse map kept for the rare callers that have a Path and want
    # to know its doc_id.  Currently unused by core but exposed for
    # forward compatibility (e.g. future per-doc score caching).
    doc_to_id: Dict[Path, int] = field(default_factory=dict)
    # v2.9: cached per-doc filename_lower string (stem with hyphens →
    # spaces, lowered) so the search hot path can do filename
    # substring matching without recomputing per doc.
    filename_lower: Dict[Path, str] = field(default_factory=dict)
    # v2.9.0 — Bloom filter (P1, exact set; "bloom" by convention only).
    # ``token_bloom`` is the union of every token that appears in any
    # document's content OR in any filename_lower tokenization.  When
    # NO query token is in ``token_bloom``, the only remaining way a
    # doc can match is via filename-substring (query_lower in
    # filename_lower).  ``filename_corpus`` is the concatenation of
    # every filename_lower string (joined with ``\x00`` so substring
    # matches can't cross file boundaries) — so we can answer
    # "is ``query_lower`` a substring of any filename?" in one
    # ``str.__contains__`` call instead of an O(N) per-doc scan.
    # Together they let ``search()`` early-exit on true no-match queries
    # before doing any candidate selection / alias expansion bookkeeping.
    token_bloom: set = field(default_factory=set)
    filename_corpus: str = ""


# ── Module-level singleton ────────────────────────────────────────
_INDEX_CACHE: Optional[CorpusIndex] = None
_INDEX_LOCK = threading.Lock()
_BUILD_HITS = 0
_BUILD_MISSES = 0
# v2.8.1: write-path generation counter.  When a write path (track,
# update, fact_add, …) mutates a markdown file it bumps this counter
# via :func:`invalidate`.  ``get_corpus_index`` records the generation
# observed at build time and short-circuits the per-file ``stat()``
# fingerprint when the generation is unchanged.  This is the fast path
# for read-heavy workloads (search >> write).
_WRITE_GENERATION: int = 0
_INDEX_GENERATION: int = -1  # generation captured when _INDEX_CACHE was built
_FAST_HITS = 0


def _compute_fingerprint(files: Iterable[Path]) -> tuple[bytes, list[tuple[Path, dict]]]:
    """Return (fingerprint, [(path, stat_tuple)…]) for the given files.

    Files that no longer exist (race with deletion) are silently
    skipped from both fingerprint and the returned list.  ``stat_tuple``
    is the (mtime_ns, size) pair — we keep it so the build phase can
    short-circuit on a stat we already did.
    """
    items: list[tuple[Path, dict]] = []
    h = hashlib.blake2b(digest_size=16)
    # Sort for determinism — fingerprint must not depend on directory
    # iteration order.
    for p in sorted(files, key=lambda x: str(x)):
        try:
            st = p.stat()
        except (FileNotFoundError, OSError):
            continue
        items.append((p, {"mtime_ns": st.st_mtime_ns, "size": st.st_size}))
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(int(st.st_mtime_ns).to_bytes(8, "little", signed=False))
        h.update(int(st.st_size).to_bytes(8, "little", signed=False))
        h.update(b"\xff")
    return h.digest(), items


def _compute_path_set_hash(files: Iterable[Path]) -> bytes:
    """v2.8.1: cheap path-set identity (no ``stat``).

    Used by the fast path to ensure the cached index belongs to the
    caller's corpus (multiple ``MemKraft`` instances may share the
    singleton in long-running processes or test suites).
    """
    h = hashlib.blake2b(digest_size=16)
    for p in sorted(files, key=lambda x: str(x)):
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.digest()


def _build_index(
    items: list[tuple[Path, dict]],
    search_tokens_fn: Callable[[str], list],
    read_text_fn: Optional[Callable[[Path], Optional[str]]],
    fingerprint: bytes,
) -> CorpusIndex:
    """Build a fresh :class:`CorpusIndex` from the given file list."""
    token_doc_freq: Dict[str, int] = {}
    doc_token_freqs: Dict[Path, Dict[str, int]] = {}
    doc_lengths: Dict[Path, int] = {}
    total_tokens = 0
    valid_docs = 0
    # v2.9 — postings (inverted index) built alongside the BM25 stats.
    # v2.9.1 — postings hold int doc_ids, not Path objects.  doc_id
    # is assigned on first encounter (sequential, starting at 0).
    content_postings: Dict[str, list] = {}
    filename_postings: Dict[str, list] = {}
    filename_lower_map: Dict[Path, str] = {}
    doc_id_map: list = []   # doc_id -> Path
    doc_to_id: Dict[Path, int] = {}  # Path -> doc_id

    for md, _stat in items:
        try:
            if read_text_fn is not None:
                text = read_text_fn(md)
                if text is None:
                    continue
                doc_text = text.lower()
            else:
                doc_text = md.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        # v2.9.1: assign doc_id for this document.  Used by postings.
        doc_id = len(doc_id_map)
        doc_id_map.append(md)
        doc_to_id[md] = doc_id

        doc_tok_list = search_tokens_fn(doc_text)
        tf_map: Dict[str, int] = {}
        for t in doc_tok_list:
            tf_map[t] = tf_map.get(t, 0) + 1
        doc_token_freqs[md] = tf_map
        doc_lengths[md] = len(doc_tok_list)
        total_tokens += len(doc_tok_list)
        for t in tf_map:
            token_doc_freq[t] = token_doc_freq.get(t, 0) + 1
            # v2.9: same iteration — build content posting list (int doc_ids).
            posting = content_postings.get(t)
            if posting is None:
                content_postings[t] = [doc_id]
            else:
                posting.append(doc_id)
        # v2.9: filename postings.  Mirror search()'s filename_lower
        # derivation (stem, hyphens→spaces, lowered) so candidate
        # selection matches scoring exactly.
        fname_lower = md.stem.lower().replace("-", " ")
        filename_lower_map[md] = fname_lower
        for ft in search_tokens_fn(fname_lower):
            posting = filename_postings.get(ft)
            if posting is None:
                filename_postings[ft] = [doc_id]
            else:
                posting.append(doc_id)
        valid_docs += 1

    # Mirror the legacy semantics: ``doc_count = max(len(all_files), 1)``
    # so BM25 IDF never divides by zero on an empty corpus.
    doc_count = max(valid_docs, 1)
    avg_doc_len = (total_tokens / doc_count) if total_tokens > 0 else 0.0

    # v2.9.0 — Bloom filter assembly.  Union of every content posting
    # key and filename posting key — i.e. every token that any doc in
    # the corpus contains.  Plus a single concatenated string of all
    # filename_lower values (NUL-separated to prevent cross-file
    # substring leakage) so the early-exit gate can answer
    # "does ``query_lower`` appear in ANY filename?" in O(1) amortized.
    _bloom: set = set()
    _bloom.update(content_postings.keys())
    _bloom.update(filename_postings.keys())
    _fn_corpus = "\x00".join(filename_lower_map.values())

    return CorpusIndex(
        doc_count=doc_count,
        avg_doc_len=avg_doc_len,
        token_doc_freq=token_doc_freq,
        doc_token_freqs=doc_token_freqs,
        doc_lengths=doc_lengths,
        fingerprint=fingerprint,
        path_set_hash=_compute_path_set_hash(p for p, _ in items),
        doc_stat={p: (s["mtime_ns"], s["size"]) for p, s in items},
        file_list=[p for p, _ in items],
        content_postings=content_postings,
        filename_postings=filename_postings,
        filename_lower=filename_lower_map,
        token_bloom=_bloom,
        filename_corpus=_fn_corpus,
        doc_id_map=doc_id_map,
        doc_to_id=doc_to_id,
    )


def get_corpus_index(
    all_md_files_fn: Callable[[], Iterable[Path]],
    search_tokens_fn: Callable[[str], list],
    read_text_fn: Optional[Callable[[Path], Optional[str]]] = None,
    trust_write_hooks: bool = False,
) -> CorpusIndex:
    """Return the cached :class:`CorpusIndex`, rebuilding on change.

    Parameters
    ----------
    all_md_files_fn:
        Zero-arg callable returning an iterable of markdown ``Path``
        objects (typically ``MemKraft._all_md_files``).
    search_tokens_fn:
        Tokenizer used for BM25 (typically ``MemKraft._search_tokens``).
    read_text_fn:
        Optional reader (e.g. ``MemKraft._safe_read``) — when supplied
        we go through the bounded LRU read cache, which makes the
        first build cheaper too.  When ``None`` we read directly via
        ``Path.read_text``.
    trust_write_hooks:
        v2.8.1.  When ``True``, the caller promises that every write
        which mutates a corpus markdown file also invokes
        :func:`invalidate` (directly or via
        ``_ReadCache.invalidate``).  This lets ``get_corpus_index``
        skip the per-file ``stat()`` fingerprint pass when no writes
        have happened since the cached build — the fast path that
        powers the search hot loop.

        When ``False`` (the default, used by tests and callers that
        mutate corpus files directly without going through MemKraft's
        write API), the legacy fingerprint check always runs so a
        bare ``Path.write_text`` outside the library still invalidates
        the cache.

    Returns
    -------
    CorpusIndex
        Treat as read-only.  Mutating it corrupts subsequent searches.
    """
    global _INDEX_CACHE, _BUILD_HITS, _BUILD_MISSES, _INDEX_GENERATION, _FAST_HITS

    if trust_write_hooks and not _disabled():
        # v2.8.1 fast path: if no writes have happened since the
        # cached index was built AND the corpus file-set hash matches
        # (same MemKraft instance / same corpus), skip the per-file
        # ``stat()`` fingerprint pass entirely.  ``invalidate()``
        # (called from write paths) bumps ``_WRITE_GENERATION``; the
        # path-set check guards against singleton bleed across
        # MemKraft instances (e.g. in long-running test runs).
        files = list(all_md_files_fn())
        fingerprint, items = _compute_fingerprint(files)
        with _INDEX_LOCK:
            cached = _INDEX_CACHE
            if (
                cached is not None
                and _INDEX_GENERATION == _WRITE_GENERATION
                and cached.path_set_hash == _compute_path_set_hash(files)
                and cached.fingerprint == fingerprint
            ):
                _FAST_HITS += 1
                _BUILD_HITS += 1
                return cached
            observed_generation = _WRITE_GENERATION
    elif not _disabled():
        # Legacy slow-but-safe path: fingerprint scan always runs.
        with _INDEX_LOCK:
            observed_generation = _WRITE_GENERATION
        files = list(all_md_files_fn())
        fingerprint, items = _compute_fingerprint(files)
    else:
        observed_generation = -1
        files = list(all_md_files_fn())
        fingerprint, items = _compute_fingerprint(files)

    if _disabled():
        # Always rebuild; do not touch the cache so unit tests can
        # observe the disabled path without state bleed.
        _BUILD_MISSES += 1
        return _build_index(items, search_tokens_fn, read_text_fn, fingerprint)

    with _INDEX_LOCK:
        cached = _INDEX_CACHE
        if cached is not None and cached.fingerprint == fingerprint:
            _BUILD_HITS += 1
            return cached

    # Build outside the lock — tokenization can be expensive on large
    # corpora and we don't want to block other threads' fingerprint
    # checks while we work.
    fresh = _build_index(items, search_tokens_fn, read_text_fn, fingerprint)

    with _INDEX_LOCK:
        # Re-check: another thread may have built the same fingerprint
        # while we were working.  If so, prefer the existing instance
        # to keep dict identity stable for downstream callers.
        existing = _INDEX_CACHE
        if existing is not None and existing.fingerprint == fingerprint:
            _BUILD_HITS += 1
            return existing
        _INDEX_CACHE = fresh
        _INDEX_GENERATION = observed_generation
        _BUILD_MISSES += 1
        return fresh


def invalidate(path: Optional[Path] = None) -> None:
    """Mark the corpus as dirty (v2.8.1 write-path hook).

    Called from write paths (``track``, ``update``, ``fact_add``, …)
    when a markdown file is created, modified, or deleted.  Bumping
    the generation counter forces the next ``get_corpus_index`` call
    to re-fingerprint and rebuild as needed.

    The ``path`` argument is accepted for forward compatibility (per-
    file partial invalidation may land in a later release) but is
    currently ignored — we always do a global bump because BM25 IDF
    depends on every other doc's TF anyway.
    """
    global _WRITE_GENERATION
    with _INDEX_LOCK:
        _WRITE_GENERATION += 1


def reset_for_tests() -> None:
    """Drop the singleton + counters.  Test helper only."""
    global _INDEX_CACHE, _BUILD_HITS, _BUILD_MISSES, _INDEX_GENERATION, _WRITE_GENERATION, _FAST_HITS
    with _INDEX_LOCK:
        _INDEX_CACHE = None
        _BUILD_HITS = 0
        _BUILD_MISSES = 0
        _INDEX_GENERATION = -1
        _WRITE_GENERATION = 0
        _FAST_HITS = 0


def stats() -> Dict[str, Any]:
    """Diagnostic counters."""
    with _INDEX_LOCK:
        cached = _INDEX_CACHE
        return {
            "cached": cached is not None,
            "doc_count": cached.doc_count if cached is not None else 0,
            "avg_doc_len": cached.avg_doc_len if cached is not None else 0.0,
            "build_hits": _BUILD_HITS,
            "build_misses": _BUILD_MISSES,
            "fast_hits": _FAST_HITS,
            "write_generation": _WRITE_GENERATION,
            "index_generation": _INDEX_GENERATION,
            "disabled": _disabled(),
        }
