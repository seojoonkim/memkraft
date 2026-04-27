"""v1.0.3 Chunking + precision search — additive, non-breaking.

Adds two new public methods to MemKraft, attached via the mixin loop in
``__init__.py``:

- ``track_document(doc_id, content, chunk_size=500, chunk_overlap=50,
  entity_type="document", source="")`` — split a long document into
  ~chunk_size-word overlapping chunks (BM25-style) and track each as
  its own entity under ``{doc_id}__c{idx}``. Returns the number of
  chunks created.

- ``search_precise(query, top_k=5, score_threshold=0.1)`` —
  run ``search(query, fuzzy=False)`` first and drop hits with score
  below ``score_threshold``; if nothing survives, fall back to
  ``search(query, fuzzy=True)`` with a relaxed threshold
  (``score_threshold * 0.5``). Returns at most ``top_k`` hits.

Empirical motivation (AMB PersonaMem, 2026-04 Zeon):
  * 32k tokens: MemKraft 80% vs BM25 70% (+10pp)
  * 128k tokens: MemKraft 75% vs BM25 50% (+25pp)

Design constraints honoured:
  * Does NOT modify core.py or the existing ``track`` / ``update`` /
    ``search`` signatures.
  * Builds on public primitives only (``self.track`` + ``self.update``
    + ``self.search``).
  * Best-effort per chunk — one failing chunk does not abort ingest.
"""
from __future__ import annotations

from typing import Any, List


def _chunk_text(text: str, size: int = 500, overlap: int = 50) -> List[str]:
    """Split ``text`` into ~size-word overlapping chunks.

    Always returns at least one chunk. If text fits within ``size``
    words, returns a single-element list with the original text.
    """
    if not text:
        return [""]
    words = text.split()
    if len(words) <= size:
        return [text]
    step = max(1, size - overlap)
    chunks: List[str] = []
    i = 0
    while i < len(words):
        piece = words[i : i + size]
        if not piece:
            break
        chunks.append(" ".join(piece))
        if i + size >= len(words):
            break
        i += step
    return chunks or [text]


class ChunkingMixin:
    """v1.0.3 additive chunking + precision search API."""

    # ------------------------------------------------------------------
    # track_document — auto-chunking for long documents
    # ------------------------------------------------------------------
    def track_document(
        self,
        doc_id: str,
        content: str,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        entity_type: str = "document",
        source: str = "",
    ) -> int:
        """Track a long document by splitting it into overlapping chunks.

        Each chunk becomes its own entity named ``{doc_id}__c{idx}`` with
        ``entity_type="chunk"`` so ``search`` can match chunk-level.
        A parent entity ``doc_id`` is also tracked (without content)
        so callers can group chunk hits back to the source document.

        Validated on AMB PersonaMem 128k: +25pp over BM25 baseline.

        Args:
            doc_id: Stable identifier for the source document.
            content: Full document text.
            chunk_size: Target chunk size in words (default 500).
            chunk_overlap: Overlap between consecutive chunks (default 50).
            entity_type: Type stored on the parent entity (default
                ``"document"``). Chunks are always typed as ``"chunk"``.
            source: Source tag propagated to ``track`` / ``update``.

        Returns:
            int: number of chunks created.
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be in [0, chunk_size)")

        # 1. Track the parent doc (no content) so callers can link chunks.
        try:
            self.track(doc_id, entity_type=entity_type, source=source)
        except Exception:
            # Parent tracking is best-effort; chunks still work without it.
            pass

        # 2. Split and track each chunk.
        chunks = _chunk_text(content or "", size=chunk_size, overlap=chunk_overlap)
        created = 0
        for idx, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            chunk_id = f"{doc_id}__c{idx}"
            try:
                self.track(chunk_id, entity_type="chunk", source=source)
                self.update(chunk_id, chunk, source=source)
                created += 1
            except Exception:
                # Best-effort: keep going even if one chunk fails.
                continue
        return created

    # ------------------------------------------------------------------
    # search_precise — score threshold + fuzzy fallback
    # ------------------------------------------------------------------
    def search_precise(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.1,
    ) -> List[dict]:
        """Precision-first search with fuzzy fallback.

        Pass 1 runs ``search(query, fuzzy=False)`` and drops hits whose
        score is below ``score_threshold``.
        Pass 2 (only if Pass 1 found nothing) runs
        ``search(query, fuzzy=True)`` with a relaxed threshold
        (``score_threshold * 0.5``) to recover recall.

        Args:
            query: Search query.
            top_k: Maximum number of hits to return (default 5).
            score_threshold: Minimum score to keep (default 0.1).

        Returns:
            list[dict]: Up to ``top_k`` hits, highest-score first.
        """
        if not query or not str(query).strip():
            return []
        if top_k <= 0:
            return []

        def _score(hit: dict) -> float:
            for key in ("score", "relevance", "rank"):
                val = hit.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        continue
            return 0.0

        # Pass 1: precision (fuzzy=False)
        try:
            primary = self.search(query, fuzzy=False) or []
        except Exception:
            primary = []
        filtered = [h for h in primary if _score(h) >= score_threshold]

        # Pass 2: fuzzy fallback with relaxed threshold
        if not filtered:
            try:
                fallback = self.search(query, fuzzy=True) or []
            except Exception:
                fallback = []
            relaxed = score_threshold * 0.5
            filtered = [h for h in fallback if _score(h) >= relaxed]

        filtered.sort(key=_score, reverse=True)
        result = filtered[:top_k]
        # v2.4.0: decay reset for search hits
        now_str = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for r in result:
            fpath = r.get("file") if isinstance(r, dict) else None
            if fpath and hasattr(self, "_touch_last_accessed"):
                try:
                    self._touch_last_accessed(fpath, now_str)
                except Exception:
                    pass
        return result
