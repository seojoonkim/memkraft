#!/usr/bin/env python3
"""Command-line runner for MemKraft Memory Gym scenarios.

Exit codes:
    0 -- scenario ran and, when ``--gate`` is given, all thresholds passed.
    1 -- gate failure: the scenario ran but at least one threshold failed.
    2 -- usage error: invalid parameters or unknown scenario; a structured
         ``{"error": {"kind", "message", "param"}}`` JSON is printed to stdout
         (and written to ``--out`` when present) instead of a traceback.
"""
from __future__ import annotations

import argparse
import json
import math
import re
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


def _finite_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid threshold: {raw!r}") from exc
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("threshold must be a finite number")
    return value


def _write_output(payload: dict, out: str | None) -> None:
    output = json.dumps(payload, indent=2)
    if out is not None:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
    print(output)


class _UsageError(Exception):
    def __init__(self, message: str, param: str | None):
        super().__init__(message)
        self.param = param


class _GymArgumentParser(argparse.ArgumentParser):
    """Parser that raises instead of exiting so errors can be reported as JSON."""

    def error(self, message: str) -> None:  # type: ignore[override]
        match = re.search(r"--([A-Za-z0-9-]+)", message)
        param = match.group(1).replace("-", "_") if match else None
        raise _UsageError(message, param)


def _extract_out(argv: Sequence[str] | None) -> str | None:
    """Best-effort ``--out`` lookup for reporting errors when parsing fails."""
    tokens = list(sys.argv[1:] if argv is None else argv)
    for index, token in enumerate(tokens):
        if token == "--out" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--out="):
            return token.split("=", 1)[1]
    return None


def _usage_error_payload(kind: str, param: str | None, message: str) -> dict:
    return {"error": {"kind": kind, "param": param, "message": message}}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _GymArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="search_recall")
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
    parser.add_argument("--min-mean-recall-at-k", type=_finite_float, default=gates.DEFAULT_GATE["min_mean_recall_at_k"])
    parser.add_argument("--min-min-recall-at-k", type=_finite_float, default=gates.DEFAULT_GATE["min_min_recall_at_k"])
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        error_payload = _usage_error_payload("invalid_parameter", exc.param, str(exc))
        _write_output(error_payload, _extract_out(argv))
        return 2

    try:
        payload = scenarios.run_scenario(
            args.scenario,
            sizes=args.sizes,
            top_k=args.top_k,
            candidate=args.candidate,
            hybrid_alpha=args.hybrid_alpha,
        )
    except ValueError as exc:
        if args.scenario in scenarios.registered_scenarios():
            error_payload = _usage_error_payload("invalid_parameter", None, str(exc))
        else:
            error_payload = _usage_error_payload("unknown_scenario", "scenario", str(exc))
        _write_output(error_payload, args.out)
        return 2
    exit_code = 0

    if args.gate:
        thresholds = {
            "min_mean_recall_at_k": args.min_mean_recall_at_k,
            "min_min_recall_at_k": args.min_min_recall_at_k,
        }
        gate = gates.evaluate_gate(payload, thresholds)
        payload["gate"] = gate
        payload["thresholds"] = thresholds
        payload["observed"] = gates.observed_metrics(payload)
        payload["pass"] = gate["passed"]
        payload["baseline_ref"] = gates.BASELINE_REF
        exit_code = 0 if gate["passed"] else 1

    _write_output(payload, args.out)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
