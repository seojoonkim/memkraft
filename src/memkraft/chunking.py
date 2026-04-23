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
        return filtered[:top_k]

    # ------------------------------------------------------------------
    # search_with_entity_filter — entity-aware retrieval (v1.1.2)
    # ------------------------------------------------------------------
    def search_with_entity_filter(
        self,
        query: str,
        top_k: int = 5,
        entity_names: "List[str] | None" = None,
        auto_extract: bool = True,
    ) -> "List[dict]":
        """Entity-aware search: filter memory by entity before attribute search.

        Algorithm:
        1. If ``auto_extract=True``, extract candidate entity names from query
           using a lightweight regex NER (capitalised words, stop-word filtered).
        2. Score every hit from ``search_precise`` by whether the entity names
           appear in the matched text — hits that mention a detected entity
           bubble to the top.
        3. If no entities are detected, falls back transparently to
           ``search_precise``.

        This significantly improves recall for persona-style queries like
        "What does Sarah like to eat?" because the entity (Sarah) is used to
        re-rank hits before returning them.

        Args:
            query: Natural-language search query.
            top_k: Maximum number of hits to return (default 5).
            entity_names: Explicit list of entity names to filter by. When
                provided, ``auto_extract`` is ignored.
            auto_extract: If True and ``entity_names`` is None, extract entity
                names from ``query`` automatically.

        Returns:
            list[dict]: Up to ``top_k`` hits, entity-boosted score first.
        """
        import re

        if not query or not str(query).strip():
            return []
        if top_k <= 0:
            return []

        # ------------------------------------------------------------------ #
        # Step 1 — determine entity names
        # ------------------------------------------------------------------ #
        entities: List[str] = list(entity_names) if entity_names else []
        if auto_extract and not entities:
            # Simple capitalised-word NER (works well for person names)
            candidates = re.findall(r"\b[A-Z][a-z]+\b", query)
            _STOPWORDS = {
                "What", "Who", "Where", "When", "How", "Why",
                "The", "Is", "Are", "Did", "Does", "Has", "Have",
                "Can", "Could", "Would", "Should", "Tell", "Me",
                "About", "Which", "With", "From", "User", "Some",
                "You", "Your", "My", "Please", "This", "That",
            }
            entities = [c for c in candidates if c not in _STOPWORDS]

        # No entities detected → plain precision search
        if not entities:
            return self.search_precise(query, top_k=top_k)

        # ------------------------------------------------------------------ #
        # Step 2 — retrieve candidates via search_precise (wider net)
        # ------------------------------------------------------------------ #
        search_k = max(top_k * 3, 15)  # cast a wider net
        candidates_hits = self.search_precise(query, top_k=search_k)

        if not candidates_hits:
            return []

        # ------------------------------------------------------------------ #
        # Step 3 — re-rank: hits that mention an entity get a score boost
        # ------------------------------------------------------------------ #
        entities_lower = [e.lower() for e in entities]

        def _score(hit: dict) -> float:
            for key in ("score", "relevance", "rank"):
                val = hit.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        continue
            return 0.0

        def _entity_boost(hit: dict) -> float:
            """1.0 if the entity appears in any hit field, else 0.0."""
            text = " ".join([
                str(hit.get("match") or ""),
                str(hit.get("content") or ""),
                str(hit.get("entity") or ""),
                str(hit.get("snippet") or ""),
                str(hit.get("file") or ""),
                str(hit.get("text") or ""),
            ]).lower()
            if any(e in text for e in entities_lower):
                return 1.0
            return 0.0

        # Composite sort key: entity match first, then original score
        ranked = sorted(
            candidates_hits,
            key=lambda h: (_entity_boost(h), _score(h)),
            reverse=True,
        )
        return ranked[:top_k]
