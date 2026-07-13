#!/usr/bin/env python3
"""Stdlib-only benchmark for cold and warm compiled-truth reads."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from memkraft import MemKraft

DEFAULT_SIZES = "100,1000"
SCHEMA = "memkraft.derived_views_benchmark"
VERSION = 1


def positive_sizes(value: str) -> List[int]:
    try:
        sizes = [int(item.strip()) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--sizes must be comma-separated positive integers") from exc
    if not sizes or any(size <= 0 for size in sizes):
        raise argparse.ArgumentTypeError("--sizes must be comma-separated positive integers")
    return sizes


def run_one(size: int, mode: str = "cache") -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        writer = MemKraft(tmp)
        with contextlib.redirect_stdout(io.StringIO()):
            for index in range(size):
                writer.append_event("bench-user", f"key-{index:08d}", index, source="derived-views-bench")
            writer.sleep(dry_run=False)

        reader = MemKraft(tmp)
        started = time.perf_counter()
        cold_truth = reader.current_truth("bench-user")
        cold_ms = (time.perf_counter() - started) * 1000.0

        if mode == "no-cache":
            reader._truth_snapshot_cache = None
            reader._event_snapshot_cache = None
        started = time.perf_counter()
        warm_truth = reader.current_truth("bench-user")
        warm_ms = (time.perf_counter() - started) * 1000.0

        if cold_truth != warm_truth or len(cold_truth) != size:
            raise RuntimeError("compiled truth benchmark produced an unexpected result")
        return {
            "events": size,
            "truth_items": len(cold_truth),
            "cold_current_truth_ms": cold_ms,
            "warm_current_truth_ms": warm_ms,
        }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=positive_sizes, default=positive_sizes(DEFAULT_SIZES))
    parser.add_argument("--mode", choices=("cache", "no-cache"), default="cache")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    payload = {
        "schema": SCHEMA,
        "version": VERSION,
        "options": {"sizes": args.sizes, **({"mode": args.mode} if args.mode != "cache" else {})},
        "results": [run_one(size, args.mode) for size in args.sizes],
    }
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        parser.error(f"cannot write --out {args.out}: {exc}")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
