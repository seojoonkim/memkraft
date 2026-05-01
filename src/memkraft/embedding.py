"""v2.7.3 — Local embedding retrieval (semantic + hybrid search).

Adds dense retrieval to MemKraft on top of the existing BM25/IDF/RRF
stack. Default model is `sentence-transformers/all-MiniLM-L6-v2`
(~90 MB on disk, 384-dim) — small enough to ship behind an optional
extra and good enough to close the LongMemEval gap from ~90% to the
MemPalace/OMEGA range (95%+).

Design constraints honoured
---------------------------
* **Additive only** — public method names below; no existing
  signature changes.
* **Optional dep** — `pip install memkraft[embedding]` installs
  `sentence-transformers`. Without it, `embed_text` /
  `search_semantic` / `search_hybrid` raise a clear
  `MemKraftEmbeddingError` with the install hint.
* **Lazy** — model is loaded on first call (singleton per process /
  per `MemKraft` instance). No import-time cost.
* **Storage** — `<base_dir>/.memkraft/embeddings/index.jsonl` (single
  file, append-friendly, one record per markdown file). Vector +
  mtime + dim + model name + file path. Mtime is used to skip
  re-encoding unchanged docs.
* **Search shape** — results carry the same keys as `search_v2`
  (`file`, `score`, `confidence`, …) so existing callers / harnesses
  can swallow them unchanged.

Public surface added to `MemKraft`
----------------------------------
* `mk.embed_text(text)` → list[float]
* `mk.embed_batch(texts)` → list[list[float]]
* `mk.search_semantic(query, top_k=10, *, min_score=0.0)`
* `mk.search_hybrid(query, top_k=10, *, alpha=0.5, k=60)` — RRF
  fusion of BM25 (`search_smart`) and semantic.
* `mk.build_embeddings(force=False)` → dict (stats)
* `mk.embedding_stats()` → dict (count, dim, model, last_built)
* `mk.embedding_clear()` → drop the on-disk index.

The mixin attaches via `__init__.py`'s normal mixin loop.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence


__all__ = [
    "EmbeddingMixin",
    "MemKraftEmbeddingError",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_EMBEDDING_DIM",
]


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DIM = 384  # informational; whatever the model emits wins
_EMBED_CACHE_CAPACITY = 256  # per-process LRU on raw text → vector
_INDEX_BASENAME = "index.jsonl"


class MemKraftEmbeddingError(RuntimeError):
    """Raised when embedding features are requested but unavailable.

    Most common cause: `sentence-transformers` is not installed.
    Install with `pip install memkraft[embedding]`.
    """


# ----------------------------------------------------------------------
# Model loader (lazy, process-wide singleton per (model_name,))
# ----------------------------------------------------------------------
_MODEL_LOCK = threading.Lock()
_MODEL_CACHE: "dict[str, Any]" = {}


def _load_st_model(model_name: str) -> Any:
    """Lazy-load a `SentenceTransformer` model (singleton per name).

    Raises `MemKraftEmbeddingError` if `sentence-transformers` is
    missing. Subsequent calls with the same name are O(1).
    """
    cached = _MODEL_CACHE.get(model_name)
    if cached is not None:
        return cached
    with _MODEL_LOCK:
        cached = _MODEL_CACHE.get(model_name)
        if cached is not None:
            return cached
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # ImportError or transient torch err
            raise MemKraftEmbeddingError(
                "sentence-transformers is not installed. "
                "Install the optional embedding extra:\n"
                "    pip install 'memkraft[embedding]'\n"
                f"(underlying error: {exc!r})"
            ) from exc
        model = SentenceTransformer(model_name)
        _MODEL_CACHE[model_name] = model
        return model


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------
def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity on plain Python lists (no numpy hard dep).

    Vectors are expected to be roughly the same length; mismatches
    return 0.0 to keep callers safe.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def _to_float_list(vec: Any) -> List[float]:
    """Coerce numpy / torch tensors / generic iterables to list[float]."""
    if vec is None:
        return []
    # numpy / torch — duck-type ``tolist``
    tolist = getattr(vec, "tolist", None)
    if callable(tolist):
        out = tolist()
        if isinstance(out, list):
            # 1-D expected; flatten one level if model returned 2-D
            if out and isinstance(out[0], list):
                out = out[0]
            return [float(x) for x in out]
    if isinstance(vec, (list, tuple)):
        if vec and isinstance(vec[0], (list, tuple)):
            vec = vec[0]
        return [float(x) for x in vec]
    raise TypeError(f"cannot coerce embedding of type {type(vec)!r} to list[float]")


# ----------------------------------------------------------------------
# Mixin
# ----------------------------------------------------------------------
class EmbeddingMixin:
    """Adds local embedding retrieval to `MemKraft`.

    Attached via `__init__.py`'s mixin loop. All state lives either
    on the instance (lazy attrs prefixed `_embedding_…`) or on disk
    under `<base_dir>/.memkraft/embeddings/`.
    """

    # ------------------------------------------------------------------
    # Lazy state accessors
    # ------------------------------------------------------------------
    def _embedding_model_name(self) -> str:
        """Resolve the model name (env override → instance → default)."""
        env = os.environ.get("MEMKRAFT_EMBEDDING_MODEL")
        if env:
            return env
        return getattr(self, "_embedding_model_name_override", None) or DEFAULT_EMBEDDING_MODEL

    def _embedding_index_path(self) -> Path:
        base = Path(getattr(self, "base_dir", Path.cwd()))
        d = base / ".memkraft" / "embeddings"
        d.mkdir(parents=True, exist_ok=True)
        return d / _INDEX_BASENAME

    def _embedding_text_cache(self) -> "OrderedDict[str, list[float]]":
        cache = getattr(self, "_embedding_text_cache_obj", None)
        if cache is None:
            cache = OrderedDict()
            self._embedding_text_cache_obj = cache
        return cache

    def _embedding_doc_cache(self) -> "dict[str, dict]":
        """In-memory mirror of the on-disk index (keyed by absolute file path)."""
        cache = getattr(self, "_embedding_doc_cache_obj", None)
        if cache is None:
            cache = {}
            self._embedding_doc_cache_obj = cache
            self._embedding_index_loaded = False
        return cache

    # ------------------------------------------------------------------
    # Public — text-level embedding
    # ------------------------------------------------------------------
    def embed_text(self, text: str) -> List[float]:
        """Embed a single string. LRU-cached on raw text.

        Raises `MemKraftEmbeddingError` if the optional extra is missing.
        """
        if not isinstance(text, str):
            raise TypeError("embed_text expects a string")
        text = text.strip()
        if not text:
            return []
        cache = self._embedding_text_cache()
        if text in cache:
            cache.move_to_end(text)
            return cache[text]
        model = _load_st_model(self._embedding_model_name())
        vec_raw = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
        vec = _to_float_list(vec_raw)
        cache[text] = vec
        if len(cache) > _EMBED_CACHE_CAPACITY:
            cache.popitem(last=False)
        return vec

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed a list of strings in one batch.

        Empty / non-string entries get an empty vector back so the
        output list is always the same length as the input.
        """
        if not texts:
            return []
        clean: list[tuple[int, str]] = []
        for i, t in enumerate(texts):
            if isinstance(t, str) and t.strip():
                clean.append((i, t.strip()))
        out: List[List[float]] = [[] for _ in texts]
        if not clean:
            return out
        # Take advantage of the text cache where possible.
        cache = self._embedding_text_cache()
        to_encode_idx: list[int] = []
        to_encode_txt: list[str] = []
        for i, t in clean:
            if t in cache:
                cache.move_to_end(t)
                out[i] = cache[t]
            else:
                to_encode_idx.append(i)
                to_encode_txt.append(t)
        if to_encode_txt:
            model = _load_st_model(self._embedding_model_name())
            vecs_raw = model.encode(
                to_encode_txt,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=32,
            )
            # vecs_raw is array-like of shape (N, dim)
            tolist = getattr(vecs_raw, "tolist", None)
            rows = tolist() if callable(tolist) else list(vecs_raw)
            for idx, txt, row in zip(to_encode_idx, to_encode_txt, rows):
                vec = [float(x) for x in row]
                out[idx] = vec
                cache[txt] = vec
                if len(cache) > _EMBED_CACHE_CAPACITY:
                    cache.popitem(last=False)
        return out

    # ------------------------------------------------------------------
    # Index I/O
    # ------------------------------------------------------------------
    def _embedding_index_load(self) -> "dict[str, dict]":
        """Load the on-disk index into memory (idempotent)."""
        cache = self._embedding_doc_cache()
        if getattr(self, "_embedding_index_loaded", False):
            return cache
        path = self._embedding_index_path()
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        fpath = rec.get("file")
                        if not fpath:
                            continue
                        cache[fpath] = rec
            except OSError:
                pass
        self._embedding_index_loaded = True
        return cache

    def _embedding_index_write(self, records: "dict[str, dict]") -> None:
        """Atomic-ish rewrite of the index file."""
        path = self._embedding_index_path()
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for rec in records.values():
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        tmp.replace(path)

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------
    def build_embeddings(self, force: bool = False) -> dict:
        """Walk the corpus and (re-)compute embeddings for every doc.

        Skips docs whose mtime + size match the existing index entry
        unless `force=True`. Returns a small stats dict.
        """
        if not hasattr(self, "_all_md_files"):
            return {"indexed": 0, "skipped": 0, "removed": 0, "error": "no _all_md_files"}
        cache = self._embedding_index_load()
        model_name = self._embedding_model_name()
        seen_paths: set[str] = set()
        to_encode: list[tuple[str, str, float, int]] = []  # (path, text, mtime, size)
        skipped = 0
        for md in self._all_md_files():
            try:
                stat = md.stat()
            except OSError:
                continue
            fpath = str(md)
            seen_paths.add(fpath)
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not text.strip():
                continue
            existing = cache.get(fpath)
            if (
                not force
                and existing
                and existing.get("model") == model_name
                and float(existing.get("mtime", -1)) == float(stat.st_mtime)
                and int(existing.get("size", -1)) == int(stat.st_size)
                and existing.get("vec")
            ):
                skipped += 1
                continue
            # Cap text length so we don't blow up tokenizer / encode.
            # SentenceTransformer truncates at 256 word-pieces by default;
            # passing 8 KB of chars is plenty.
            snippet = text[:8000]
            to_encode.append((fpath, snippet, float(stat.st_mtime), int(stat.st_size)))
        # Batch-encode the dirty docs.
        indexed = 0
        if to_encode:
            vecs = self.embed_batch([t for _, t, _, _ in to_encode])
            for (fpath, _txt, mtime, size), vec in zip(to_encode, vecs):
                if not vec:
                    continue
                cache[fpath] = {
                    "file": fpath,
                    "model": model_name,
                    "dim": len(vec),
                    "mtime": mtime,
                    "size": size,
                    "vec": vec,
                }
                indexed += 1
        # Drop entries whose underlying files no longer exist.
        removed = 0
        if seen_paths:
            stale = [p for p in cache.keys() if p not in seen_paths]
            for p in stale:
                cache.pop(p, None)
                removed += 1
        # Persist.
        if indexed or removed:
            self._embedding_index_write(cache)
        self._embedding_last_built = time.time()
        return {
            "indexed": indexed,
            "skipped": skipped,
            "removed": removed,
            "total": len(cache),
            "model": model_name,
        }

    def embedding_stats(self) -> dict:
        """Return a small dict describing the current embedding index."""
        cache = self._embedding_index_load()
        dims = {rec.get("dim") for rec in cache.values() if rec.get("dim")}
        return {
            "count": len(cache),
            "dim": next(iter(dims), DEFAULT_EMBEDDING_DIM) if dims else 0,
            "model": self._embedding_model_name(),
            "index_path": str(self._embedding_index_path()),
            "last_built": getattr(self, "_embedding_last_built", None),
        }

    def embedding_clear(self) -> None:
        """Drop the on-disk index and the in-memory mirrors."""
        path = self._embedding_index_path()
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
        self._embedding_doc_cache_obj = {}
        self._embedding_index_loaded = True
        self._embedding_text_cache_obj = OrderedDict()

    # ------------------------------------------------------------------
    # Search — semantic
    # ------------------------------------------------------------------
    def search_semantic(
        self,
        query: str,
        top_k: int = 10,
        *,
        min_score: float = 0.0,
        auto_build: bool = True,
    ) -> List[dict]:
        """Cosine-similarity search over the embedding index.

        Lazy-builds the index on first call (`auto_build=True`).
        Result shape mirrors `search_v2`:
            {file, score, confidence, snippet, retrieval: 'semantic'}
        """
        if not isinstance(query, str) or not query.strip():
            return []
        cache = self._embedding_index_load()
        if not cache and auto_build:
            self.build_embeddings()
            cache = self._embedding_doc_cache()
        if not cache:
            return []
        qvec = self.embed_text(query)
        if not qvec:
            return []
        scored: list[tuple[float, dict]] = []
        for rec in cache.values():
            vec = rec.get("vec")
            if not vec:
                continue
            sim = _cosine(qvec, vec)
            if sim < min_score:
                continue
            scored.append((sim, rec))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[dict] = []
        for sim, rec in scored[: max(int(top_k), 1)]:
            fpath = rec.get("file", "")
            snippet = ""
            try:
                snippet = Path(fpath).read_text(encoding="utf-8", errors="replace")[:300]
            except OSError:
                pass
            out.append({
                "file": fpath,
                "score": float(sim),
                "confidence": float(sim),  # cosine on normalised vecs ∈ [-1, 1]
                "snippet": snippet,
                "retrieval": "semantic",
            })
        return out

    # ------------------------------------------------------------------
    # Search — hybrid (BM25 ⊕ semantic via RRF)
    # ------------------------------------------------------------------
    def search_hybrid(
        self,
        query: str,
        top_k: int = 10,
        *,
        alpha: float = 0.5,
        k: int = 60,
        date_hint: Optional[str] = None,
    ) -> List[dict]:
        """Hybrid retrieval: BM25 (`search_smart`) ⊕ semantic.

        Uses Reciprocal Rank Fusion (k=60 default) with `alpha`
        weighting the semantic side. `alpha=0.0` ≡ BM25 only;
        `alpha=1.0` ≡ semantic only; `alpha=0.5` (default) is the
        even mix.

        Falls back gracefully when the embedding extra is missing —
        emits BM25 results as if `alpha=0`.
        """
        if not isinstance(query, str) or not query.strip():
            return []
        # BM25 side — prefer search_smart, fall back to search_v2.
        bm25_results: list[dict] = []
        try:
            if hasattr(self, "search_smart"):
                bm25_results = self.search_smart(
                    query,
                    top_k=max(top_k * 3, 30),
                    date_hint=date_hint,
                )
            elif hasattr(self, "search_v2"):
                bm25_results = self.search_v2(query, top_k=max(top_k * 3, 30))
        except Exception:
            bm25_results = []
        # Semantic side — be lenient about missing extras.
        sem_results: list[dict] = []
        try:
            sem_results = self.search_semantic(query, top_k=max(top_k * 3, 30))
        except MemKraftEmbeddingError:
            # Optional extra missing — fall back to pure BM25.
            return bm25_results[:top_k]
        # Clamp alpha.
        try:
            a = float(alpha)
        except (TypeError, ValueError):
            a = 0.5
        a = max(0.0, min(1.0, a))
        bm25_w = 1.0 - a
        sem_w = a
        # RRF fusion.
        scores: dict[str, float] = {}
        meta: dict[str, dict] = {}
        for rank, r in enumerate(bm25_results):
            f = r.get("file") if isinstance(r, dict) else None
            if not f:
                continue
            scores[f] = scores.get(f, 0.0) + bm25_w * (1.0 / (k + rank + 1))
            meta.setdefault(f, dict(r))
        for rank, r in enumerate(sem_results):
            f = r.get("file") if isinstance(r, dict) else None
            if not f:
                continue
            scores[f] = scores.get(f, 0.0) + sem_w * (1.0 / (k + rank + 1))
            existing = meta.get(f)
            if existing is None:
                meta[f] = dict(r)
            else:
                # Annotate that this hit was found by both sides.
                existing.setdefault("retrieval", r.get("retrieval", "bm25"))
                if "semantic_score" not in existing and "score" in r:
                    existing["semantic_score"] = r["score"]
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        out: list[dict] = []
        for f, fused in ranked[: max(int(top_k), 1)]:
            rec = meta.get(f, {"file": f}).copy()
            rec["score"] = float(fused)
            rec.setdefault("retrieval", "hybrid")
            rec["retrieval"] = "hybrid"
            # confidence: clamp to [0, 1] using a soft heuristic so
            # callers that read `confidence` (the v2.4 wrappers do)
            # see a meaningful number.
            rec["confidence"] = min(1.0, max(0.0, fused * (k + 1)))
            out.append(rec)
        return out
