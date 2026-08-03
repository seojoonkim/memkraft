#!/usr/bin/env python3
"""v3.2 — one-file incremental sync vs full rebuild.

Quantifies the *structural* work (files read for BM25, files encoded for
embeddings) that a single-file change costs versus a full rebuild, plus
wall-clock for the same operations.

Structural counts are the headline number because they are exact and
deterministic.  Wall-clock is reported too, but at these corpus sizes it
is noisy and dominated by process-level effects — do not read a speedup
claim into it that the structural counts don't already support.

No network, no optional dependencies: the embedding side runs against a
deterministic fake model injected into the embedding model cache, so the
counts measure MemKraft's I/O and index bookkeeping rather than a
particular transformer's throughput.

Usage:
    python benchmarks/live_sync_bench.py --docs 200 --json out.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memkraft import MemKraft  # noqa: E402
from memkraft import _corpus_index, embedding  # noqa: E402


BENCH_MODEL = "live-sync-bench-fake"
BENCH_DIM = 32


def _vec(text: str, dim: int = BENCH_DIM):
    """Deterministic unit vector derived from the text's sha256."""
    digest = hashlib.sha256(text.encode("utf-8", "replace")).digest()
    raw = [((digest[i % len(digest)] + i) % 251) / 251.0 for i in range(dim)]
    norm = sum(x * x for x in raw) ** 0.5 or 1.0
    return [x / norm for x in raw]


class _FakeModel:
    """Deterministic, dependency-free stand-in for a sentence encoder."""

    def __init__(self, dim: int = BENCH_DIM) -> None:
        self.dim = dim
        self.encode_calls = 0
        self.encoded_texts = 0

    def encode(self, texts, **kwargs):
        single = isinstance(texts, str)
        batch = [texts] if single else list(texts)
        self.encode_calls += 1
        self.encoded_texts += len(batch)
        out = [_vec(t, self.dim) for t in batch]
        return out[0] if single else out


def install_fake_model(name: str = BENCH_MODEL) -> _FakeModel:
    """Register the deterministic fake encoder under ``name``."""
    model = _FakeModel()
    embedding._MODEL_CACHE[name] = model
    return model


def build_corpus(root: Path, docs: int) -> MemKraft:
    """Create ``docs`` markdown notes under ``root`` and return a MemKraft."""
    mk = MemKraft(base_dir=str(root))
    mk.live_notes_dir.mkdir(parents=True, exist_ok=True)
    for i in range(docs):
        path = mk.live_notes_dir / f"bench-doc-{i:05d}.md"
        path.write_text(
            f"# Bench doc {i}\n\n"
            f"topic-{i % 7} project alpha beta gamma\n"
            f"body line for document number {i}\n",
            encoding="utf-8",
        )
    return mk


def measure_bm25(mk: MemKraft, docs: int) -> dict:
    """Full BM25 build vs a single-file incremental update."""
    reads = {"n": 0}

    def counting_read(path):
        reads["n"] += 1
        return mk._safe_read(path)

    def build():
        return _corpus_index.get_corpus_index(
            lambda: mk._all_md_files(),
            mk._search_tokens,
            counting_read,
            trust_write_hooks=True,
        )

    # ── full rebuild (cold cache) ──
    _corpus_index.reset_for_tests()
    reads["n"] = 0
    t0 = time.perf_counter()
    build()
    full_ms = (time.perf_counter() - t0) * 1000.0
    full_reads = reads["n"]

    # ── one-file incremental ──
    target = sorted(mk.live_notes_dir.glob("*.md"))[docs // 2]
    target.write_text(
        target.read_text(encoding="utf-8") + "\nappended delta line for incremental sync\n",
        encoding="utf-8",
    )
    mk.live_sync_apply(target, "modify", embeddings=False, provenance=False)
    reads["n"] = 0
    t0 = time.perf_counter()
    build()
    inc_ms = (time.perf_counter() - t0) * 1000.0
    inc_reads = reads["n"]

    return {
        "full_rebuild": {"files_read": full_reads, "wall_ms": round(full_ms, 3)},
        "incremental": {"files_read": inc_reads, "wall_ms": round(inc_ms, 3)},
        "files_read_ratio": round(full_reads / inc_reads, 2) if inc_reads else None,
    }


def measure_embedding(mk: MemKraft, docs: int, model: _FakeModel) -> dict:
    """Full embedding build vs a single-path embedding sync."""
    model.encoded_texts = 0
    t0 = time.perf_counter()
    mk.build_embeddings(force=True)
    full_ms = (time.perf_counter() - t0) * 1000.0
    full_encoded = model.encoded_texts

    target = sorted(mk.live_notes_dir.glob("*.md"))[docs // 3]
    target.write_text(
        target.read_text(encoding="utf-8") + "\nanother delta line for embedding sync\n",
        encoding="utf-8",
    )
    model.encoded_texts = 0
    # Bypass the text LRU so the measurement reflects the index path,
    # not a cache hit on identical text.
    mk._embedding_text_cache_obj = None
    t0 = time.perf_counter()
    mk.embedding_sync_path(target, "modify")
    inc_ms = (time.perf_counter() - t0) * 1000.0
    inc_encoded = model.encoded_texts

    return {
        "full_rebuild": {"files_encoded": full_encoded, "wall_ms": round(full_ms, 3)},
        "incremental": {"files_encoded": inc_encoded, "wall_ms": round(inc_ms, 3)},
        "files_encoded_ratio": round(full_encoded / inc_encoded, 2) if inc_encoded else None,
    }


def run_benchmark(docs: int = 100, root=None) -> dict:
    """Run both halves and return the machine-readable result dict."""
    cleanup = False
    if root is None:
        root = Path(tempfile.mkdtemp(prefix="memkraft-live-sync-bench-"))
        cleanup = True
    root = Path(root)
    # The fake encoder is selected via the per-instance override, which
    # the env var would otherwise shadow.
    os.environ.pop("MEMKRAFT_EMBEDDING_MODEL", None)
    model = install_fake_model()
    try:
        mk = build_corpus(root / "memory", docs)
        mk._embedding_model_name_override = BENCH_MODEL
        bm25 = measure_bm25(mk, docs)
        emb = measure_embedding(mk, docs, model)
    finally:
        _corpus_index.reset_for_tests()
        if cleanup:
            shutil.rmtree(root, ignore_errors=True)

    return {
        "benchmark": "live_sync",
        "docs": docs,
        "bm25": bm25,
        "embedding": emb,
        "limits": [
            "structural counts (files_read / files_encoded) are exact; wall_ms is indicative only",
            "both paths still stat() every corpus file to fingerprint it — the saving is in reads/encodes, not stats",
            "the embedding side uses a deterministic fake encoder, so wall_ms excludes real model inference cost",
            "a smaller output payload is never counted as a speedup",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=int, default=100)
    parser.add_argument("--json", default="", help="Write the result dict to this path")
    args = parser.parse_args(argv)

    result = run_benchmark(docs=args.docs)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
