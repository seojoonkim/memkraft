#!/usr/bin/env python3
"""Command-line runner for MemKraft Memory Gym scenarios."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from benchmarks.gym import gates, scenarios
else:  # pragma: no cover - exercised by module execution rather than script execution.
    from . import gates, scenarios


def _parse_sizes(raw: str) -> list[int]:
    sizes: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            size = int(part)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid corpus size: {part!r}") from exc
        if size <= 0:
            raise argparse.ArgumentTypeError("corpus sizes must be positive integers")
        sizes.append(size)
    if not sizes:
        raise argparse.ArgumentTypeError("at least one corpus size is required")
    return sizes


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {raw!r}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return value


def _hybrid_alpha(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid hybrid alpha: {raw!r}") from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError("hybrid alpha must be finite and between 0.0 and 1.0")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["search_recall"], default="search_recall")
    parser.add_argument("--sizes", type=_parse_sizes, default=_parse_sizes("20"))
    parser.add_argument("--top-k", type=_positive_int, default=20)
    parser.add_argument("--candidate", choices=["baseline", "legacy", "hybrid"], default="baseline")
    parser.add_argument(
        "--hybrid-alpha",
        type=_hybrid_alpha,
        default=0.025,
        help="Semantic weight for the Gym hybrid candidate. Conservative default preserves lexical recall.",
    )
    parser.add_argument("--out", required=True)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--min-mean-recall-at-k", type=float, default=gates.DEFAULT_GATE["min_mean_recall_at_k"])
    parser.add_argument("--min-min-recall-at-k", type=float, default=gates.DEFAULT_GATE["min_min_recall_at_k"])
    args = parser.parse_args(argv)

    payload = scenarios.run_scenario(
        args.scenario,
        sizes=args.sizes,
        top_k=args.top_k,
        candidate=args.candidate,
        hybrid_alpha=args.hybrid_alpha,
    )
    exit_code = 0

    if args.gate:
        gate = gates.evaluate_gate(
            payload,
            {
                "min_mean_recall_at_k": args.min_mean_recall_at_k,
                "min_min_recall_at_k": args.min_min_recall_at_k,
            },
        )
        payload["gate"] = gate
        exit_code = 0 if gate["passed"] else 1

    output = json.dumps(payload, indent=2)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")
    print(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
