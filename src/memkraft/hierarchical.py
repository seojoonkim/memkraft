"""v1.1.2 HierarchicalMixin: summary + raw dual-layer memory.

Adds two new public methods to MemKraft, attached via the mixin loop in
``__init__.py``:

- ``track_hierarchical(text, entity_name=None, chunk_size=512)`` —
  Indexes raw chunks via ``track_document`` AND creates a per-entity
  keyword index stored in ``{base_dir}/summaries/{entity}.md``.
  The summary extracts USER-turn content and builds a compact
  keyword-rich representation for fast retrieval.

- ``search_hierarchical(query, top_k=5)`` —
  Two-pass retrieval: first scan summary files for keyword overlap
  (fast, high-precision entity matching), then merge with
  ``search_precise`` results using score fusion.

Design constraints honoured:
  * Does NOT modify core.py.
  * Builds on public primitives only.
  * Additive — existing APIs unaffected.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, List


def _extract_user_turns(text: str) -> str:
    """Extract only USER turn content from a conversation.
    
    PersonaMem data has [USER] / [ASSISTANT] / [SYSTEM] markers.
    We want user preferences and facts.
    """
    if not text:
        return text
    
    # If there are turn markers, extract USER content
    if '[USER]' in text or '[ASSISTANT]' in text:
        parts = re.split(r'(\[(?:USER|ASSISTANT|SYSTEM)\])', text)
        user_parts: list[str] = []
        keep = False
        for part in parts:
            if part == '[USER]':
                keep = True
            elif part in ('[ASSISTANT]', '[SYSTEM]'):
                keep = False
            elif keep:
                user_parts.append(part.strip())
        return ' '.join(user_parts)
    
    return text


def _extract_keywords(text: str, max_keywords: int = 50) -> list[str]:
    """Extract meaningful keywords from text for summary indexing.
    
    Focuses on nouns, activities, preferences — the stuff that matters
    for persona-style queries.
    """
    if not text:
        return []
    
    # Common stopwords to skip
    _STOPS = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'can', 'shall', 'must', 'need',
        'i', 'me', 'my', 'mine', 'we', 'our', 'us', 'you', 'your', 'yours',
        'he', 'she', 'it', 'they', 'them', 'his', 'her', 'its', 'their',
        'this', 'that', 'these', 'those', 'what', 'which', 'who', 'whom',
        'how', 'when', 'where', 'why', 'if', 'then', 'than', 'so',
        'and', 'but', 'or', 'not', 'no', 'yes', 'very', 'too', 'also',
        'just', 'only', 'more', 'most', 'some', 'any', 'all', 'each',
        'every', 'both', 'few', 'many', 'much', 'other', 'another',
        'for', 'with', 'about', 'from', 'into', 'through', 'during',
        'before', 'after', 'above', 'below', 'between', 'under', 'over',
        'to', 'of', 'in', 'on', 'at', 'by', 'as', 'up', 'out', 'off',
        'really', 'actually', 'think', 'know', 'like', 'feel', 'want',
        'get', 'got', 'make', 'made', 'go', 'going', 'come', 'say',
        'said', 'tell', 'told', 'ask', 'asked', 'thing', 'things',
        'something', 'anything', 'everything', 'nothing',
        'user', 'assistant', 'system', 'sure', 'well', 'yeah',
        'okay', 'right', 'now', 'here', 'there', 'always', 'never',
        'still', 'even', 'back', 'way', 'lot', 'much', 'one', 'two',
    }
    
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    # Count frequency, skip stopwords
    freq: dict[str, int] = {}
    for w in words:
        if w not in _STOPS:
            freq[w] = freq.get(w, 0) + 1
    
    # Sort by frequency, return top keywords
    sorted_kw = sorted(freq.items(), key=lambda x: -x[1])
    return [w for w, _ in sorted_kw[:max_keywords]]


class HierarchicalMixin:
    """Summary + raw dual-layer memory for improved retrieval quality."""

    # ------------------------------------------------------------------
    # track_hierarchical — ingest with keyword summary extraction
    # ------------------------------------------------------------------
    def track_hierarchical(
        self,
        text: str,
        entity_name: str | None = None,
        chunk_size: int = 512,
    ) -> dict:
        """Index a document into both raw-chunk and keyword-summary layers.

        Args:
            text: Full document text to index.
            entity_name: Optional entity name to use as key.
            chunk_size: Word-level chunk size for raw layer (default 512).

        Returns:
            dict with ``raw`` (track_document result) and ``summary``
            (path to summary file).
        """
        # --- Raw layer (existing chunking infrastructure) ---
        doc_id = (
            entity_name.replace(" ", "_")
            if entity_name
            else hashlib.md5(text[:200].encode()).hexdigest()[:12]
        )
        doc_result = self.track_document(
            doc_id, text, chunk_size=chunk_size, entity_type="document", source="hier"
        )

        # --- Summary layer: keyword index from user turns ---
        user_text = _extract_user_turns(text)
        keywords = _extract_keywords(user_text, max_keywords=60)
        
        summary_dir = os.path.join(self.base_dir, "summaries")
        os.makedirs(summary_dir, exist_ok=True)

        key = doc_id
        summary_path = os.path.join(summary_dir, f"{key}.md")

        # Build keyword summary: entity name + keywords + first 200 chars of user text
        prefix = f"[{entity_name}] " if entity_name else ""
        summary_content = prefix + " ".join(keywords)
        # Also include a snippet of actual content for context
        snippet = user_text[:300].replace("\n", " ").strip() if user_text else ""
        if snippet:
            summary_content += "\n" + snippet

        existing = ""
        if os.path.exists(summary_path):
            try:
                existing = open(summary_path, "r").read()
            except Exception:
                existing = ""

        with open(summary_path, "w") as f:
            merged = (existing + "\n" + summary_content).strip() if existing else summary_content
            f.write(merged)

        return {"raw": doc_result, "summary": summary_path}

    # ------------------------------------------------------------------
    # _extract_key_facts — kept for backward compatibility
    # ------------------------------------------------------------------
    def _extract_key_facts(
        self, text: str, entity_name: str | None = None
    ) -> str:
        """Extract key relational facts from text using regex patterns."""
        facts: list[str] = []
        target = entity_name.lower() if entity_name else None

        patterns = [
            r"(\w+)\s+(?:works?|worked)\s+(?:at|for)\s+([A-Z]\w+(?:\s+[A-Z]\w+)*)",
            r"(\w+)\s+(?:lives?|lived|moved)\s+(?:in|to)\s+([A-Z]\w+(?:\s+[A-Z]\w+)*)",
            r"(\w+)\s+(?:likes?|loved?|enjoys?|prefers?|hates?)\s+(\w+(?:\s+\w+){0,3})",
            r"(\w+)\s+(?:is|was)\s+(?:a|an)\s+(\w+(?:\s+\w+){0,2})",
        ]

        for line in text.split("\n"):
            for pattern in patterns:
                for match in re.finditer(pattern, line, re.IGNORECASE):
                    groups = match.groups()
                    if len(groups) >= 2:
                        subject, obj = groups[0], groups[-1]
                        if target and target not in subject.lower():
                            continue
                        fact = f"{subject}: {obj}"
                        if fact not in facts:
                            facts.append(fact)

        prefix = f"[{entity_name}] " if entity_name else ""
        if facts:
            return prefix + " | ".join(facts[:15])
        return prefix + text[:300].replace("\n", " ").strip()

    # ------------------------------------------------------------------
    # search_hierarchical — dual-layer retrieval with score fusion
    # ------------------------------------------------------------------
    def search_hierarchical(
        self, query: str, top_k: int = 5
    ) -> list[str]:
        """Two-pass retrieval: summaries first, then raw chunks.

        Pass 1: Scan summary files for keyword overlap (high-precision).
        Pass 2: Backfill with ``search_precise`` results (recall).

        Returns up to ``top_k`` text snippets, de-duplicated.
        """
        results: list[str] = []
        seen: set[str] = set()

        summary_dir = os.path.join(self.base_dir, "summaries")

        if os.path.exists(summary_dir):
            query_words = set(re.findall(r"\b\w{3,}\b", query.lower()))

            scored: list[tuple[float, str]] = []
            for fname in os.listdir(summary_dir):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(summary_dir, fname)
                try:
                    content = open(fpath, "r").read()
                except Exception:
                    continue
                if not content.strip():
                    continue

                content_lower = content.lower()
                matches = sum(1 for w in query_words if w in content_lower)
                if matches > 0:
                    score = matches / max(len(query_words), 1)
                    scored.append((score, content))

            scored.sort(key=lambda x: -x[0])

            for _, content in scored[:top_k]:
                dedup_key = content[:80]
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    results.append(content)

        # Backfill with search_precise
        if len(results) < top_k:
            remaining = top_k - len(results)
            try:
                raw_hits = self.search_precise(query, top_k=remaining) or []
            except Exception:
                raw_hits = []

            for hit in raw_hits:
                text = ""
                if isinstance(hit, dict):
                    text = str(
                        hit.get("content")
                        or hit.get("match")
                        or hit.get("snippet")
                        or hit.get("text")
                        or ""
                    )
                elif isinstance(hit, str):
                    text = hit
                else:
                    text = str(hit)

                if not text.strip():
                    continue
                dedup_key = text[:80]
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    results.append(text)

        return results[:top_k]
