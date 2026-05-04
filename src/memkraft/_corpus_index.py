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


# ── Module-level singleton ────────────────────────────────────────
_INDEX_CACHE: Optional[CorpusIndex] = None
_INDEX_LOCK = threading.Lock()
_BUILD_HITS = 0
_BUILD_MISSES = 0


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
        doc_tok_list = search_tokens_fn(doc_text)
        tf_map: Dict[str, int] = {}
        for t in doc_tok_list:
            tf_map[t] = tf_map.get(t, 0) + 1
        doc_token_freqs[md] = tf_map
        doc_lengths[md] = len(doc_tok_list)
        total_tokens += len(doc_tok_list)
        for t in tf_map:
            token_doc_freq[t] = token_doc_freq.get(t, 0) + 1
        valid_docs += 1

    # Mirror the legacy semantics: ``doc_count = max(len(all_files), 1)``
    # so BM25 IDF never divides by zero on an empty corpus.
    doc_count = max(valid_docs, 1)
    avg_doc_len = (total_tokens / doc_count) if total_tokens > 0 else 0.0

    return CorpusIndex(
        doc_count=doc_count,
        avg_doc_len=avg_doc_len,
        token_doc_freq=token_doc_freq,
        doc_token_freqs=doc_token_freqs,
        doc_lengths=doc_lengths,
        fingerprint=fingerprint,
    )


def get_corpus_index(
    all_md_files_fn: Callable[[], Iterable[Path]],
    search_tokens_fn: Callable[[str], list],
    read_text_fn: Optional[Callable[[Path], Optional[str]]] = None,
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

    Returns
    -------
    CorpusIndex
        Treat as read-only.  Mutating it corrupts subsequent searches.
    """
    global _INDEX_CACHE, _BUILD_HITS, _BUILD_MISSES

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
        _BUILD_MISSES += 1
        return fresh


def reset_for_tests() -> None:
    """Drop the singleton + counters.  Test helper only."""
    global _INDEX_CACHE, _BUILD_HITS, _BUILD_MISSES
    with _INDEX_LOCK:
        _INDEX_CACHE = None
        _BUILD_HITS = 0
        _BUILD_MISSES = 0


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
            "disabled": _disabled(),
        }
