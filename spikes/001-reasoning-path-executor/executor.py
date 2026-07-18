"""Standalone allowlisted executor for trusted, validated reasoning procedures.

This disposable spike intentionally has no dependency on benchmark answer machinery.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Literal, Match, Optional

Status = Literal["executed", "fallback"]
MAX_DIGITS = 100
MAX_ITEMS = 8
MAX_DECLARED_PRIME = 1_000_000
MAX_GRID_SIDE = 1_000


@dataclass(frozen=True)
class ExecutionResult:
    status: Status
    answer: Optional[str]
    procedure_id: Optional[str]
    reason: str


@dataclass(frozen=True)
class _Procedure:
    pattern: re.Pattern[str]
    run: Callable[[Match[str]], int]


def _integer(text: str, *, positive: bool = False) -> int:
    if not text or len(text) > MAX_DIGITS or not text.isascii() or not text.isdecimal():
        raise ValueError("invalid or overlong decimal integer")
    value = int(text)
    if positive and value <= 0:
        raise ValueError("integer must be positive")
    return value


def _is_prime(value: int) -> bool:
    if value < 2 or value > MAX_DECLARED_PRIME:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def _run_a(match: Match[str]) -> int:
    limit = _integer(match["limit"], positive=True)
    text = match["divisors"]
    if " or " in text and "," not in text:
        pieces = text.split(" or ")
    else:
        pieces = re.split(r", or |, ", text)
    divisors = [_integer(piece, positive=True) for piece in pieces]
    if not 2 <= len(divisors) <= MAX_ITEMS:
        raise ValueError("divisor count outside bounds")
    if len(divisors) != len(set(divisors)) or any(value == 1 for value in divisors):
        raise ValueError("duplicate or trivial divisor")

    total = 0
    # At most 2**MAX_ITEMS - 1 combinations; no input-sized iteration.
    for mask in range(1, 1 << len(divisors)):
        common = 1
        selected = 0
        for index, divisor in enumerate(divisors):
            if mask & (1 << index):
                common = math.lcm(common, divisor)
                selected += 1
        count = (limit - 1) // common
        subtotal = common * count * (count + 1) // 2
        total += subtotal if selected % 2 else -subtotal
    return total


def _run_b_trailing(match: Match[str]) -> int:
    return _legendre(_integer(match["n"], positive=True), 5)


def _run_b_exponent(match: Match[str]) -> int:
    n = _integer(match["n"], positive=True)
    prime = _integer(match["prime"], positive=True)
    if not _is_prime(prime):
        raise ValueError("declared base is not an accepted prime")
    return _legendre(n, prime)


def _legendre(n: int, prime: int) -> int:
    total = 0
    while n:
        n //= prime
        total += n
    return total


def _run_c(match: Match[str]) -> int:
    rows = _integer(match["rows"], positive=True)
    columns = _integer(match["columns"], positive=True)
    if rows > MAX_GRID_SIDE or columns > MAX_GRID_SIDE:
        raise ValueError("grid exceeds resource bound")
    return math.comb(rows + columns, rows)


def _run_d(match: Match[str]) -> int:
    pieces = match["factors"].split(" * ")
    if not 1 <= len(pieces) <= MAX_ITEMS:
        raise ValueError("factor count outside bounds")
    primes: list[int] = []
    result = 1
    for piece in pieces:
        base_text, exponent_text = piece.split("^")
        base = _integer(base_text, positive=True)
        exponent = _integer(exponent_text, positive=True)
        if not _is_prime(base):
            raise ValueError("factor base is not an accepted prime")
        primes.append(base)
        result *= exponent + 1
    if len(primes) != len(set(primes)):
        raise ValueError("duplicate prime factor")
    return result


def _run_e(match: Match[str]) -> int:
    n = _integer(match["n"], positive=True)
    if match["power"] == "squares":
        return n * (n + 1) * (2 * n + 1) // 6
    return (n * (n + 1) // 2) ** 2


def _run_f(match: Match[str]) -> int:
    base = _integer(match["base"])
    exponent = _integer(match["exponent"])
    modulus = _integer(match["modulus"], positive=True)
    if modulus <= 1:
        raise ValueError("modulus must exceed one")
    return pow(base, exponent, modulus)


_A_DIVISORS = r"(?P<divisors>\d+(?: or \d+|(?:, \d+)+, or \d+))"
PROCEDURES: dict[str, _Procedure] = {
    "A.inclusion_exclusion_sum": _Procedure(
        re.compile(rf"Sum positive integers below (?P<limit>\d+) divisible by {_A_DIVISORS}\."),
        _run_a,
    ),
    "B.legendre_factorial_exponent": _Procedure(
        re.compile(
            r"(?:Find the trailing zeroes in (?P<trailing_n>\d+) factorial\.|"
            r"Find the exponent of (?P<prime>\d+) in the prime factorization of "
            r"(?P<exponent_n>\d+) factorial\.)"
        ),
        # Dispatch is needed because Python requires unique group names across alternatives.
        lambda match: _run_b_trailing_proxy(match)
        if match["trailing_n"] is not None
        else _run_b_exponent_proxy(match),
    ),
    "C.shortest_grid_paths": _Procedure(
        re.compile(r"Count shortest right/down paths across a (?P<rows>\d+) by (?P<columns>\d+) grid\."),
        _run_c,
    ),
    "D.divisor_count_prime_powers": _Procedure(
        re.compile(r"Count positive divisors of (?P<factors>\d+\^\d+(?: \* \d+\^\d+)*)\."),
        _run_d,
    ),
    "E.sum_squares_or_cubes": _Procedure(
        re.compile(r"Sum the (?P<power>squares|cubes) from 1 through (?P<n>\d+)\."),
        _run_e,
    ),
    "F.modular_exponentiation": _Procedure(
        re.compile(r"Compute (?P<base>\d+)\^(?P<exponent>\d+) modulo (?P<modulus>\d+)\."),
        _run_f,
    ),
}


def _run_b_trailing_proxy(match: Match[str]) -> int:
    n = _integer(match["trailing_n"], positive=True)
    return _legendre(n, 5)


def _run_b_exponent_proxy(match: Match[str]) -> int:
    n = _integer(match["exponent_n"], positive=True)
    prime = _integer(match["prime"], positive=True)
    if not _is_prime(prime):
        raise ValueError("declared base is not an accepted prime")
    return _legendre(n, prime)


def execute_validated_path(
    task: str, *, procedure_id: str, trusted: bool
) -> ExecutionResult:
    """Execute only an exact grammar associated with a trusted allowlisted ID."""
    procedure = PROCEDURES.get(procedure_id)
    if not trusted:
        return ExecutionResult("fallback", None, procedure_id if procedure else None, "untrusted provenance")
    if procedure is None:
        return ExecutionResult("fallback", None, None, "procedure ID is not allowlisted")

    match = procedure.pattern.fullmatch(task)
    if match is None:
        return ExecutionResult("fallback", None, procedure_id, "task does not exactly match procedure grammar")
    try:
        answer = procedure.run(match)
        return ExecutionResult("executed", str(answer), procedure_id, "validated procedure executed")
    except (ValueError, OverflowError):
        return ExecutionResult("fallback", None, procedure_id, "parsed inputs violate safety constraints")
