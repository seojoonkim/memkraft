"""Deterministic, leakage-checked task catalog for the expanded benchmark."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations
from typing import Callable, Optional, Union


@dataclass(frozen=True)
class ReasoningCase:
    case_id: str
    task: str
    expected: str
    lesson: Optional[str]
    family: str
    difficulty: str
    split: str
    expects_injection: bool
    answer_fn: Callable[[], str] = field(compare=False, repr=False)
    seed_family: Optional[str] = None


def _case(family: str, split: str, difficulty: str, task: str,
          operation: Callable[[], Union[int, str]], lesson: Optional[str]) -> ReasoningCase:
    case_id = f"{family.lower()}-{split}-{difficulty}"

    def answer() -> str:
        return str(operation())

    return ReasoningCase(
        case_id=case_id, task=task, expected=answer(), lesson=lesson,
        family=family, difficulty=difficulty, split=split,
        expects_injection=family != "G", answer_fn=answer,
        seed_family=family if family != "G" else None,
    )


def _sum_multiples_below(limit: int, divisors: tuple[int, ...]) -> int:
    """Inclusion-exclusion over arithmetic series, without materializing sets."""
    total = 0
    for size in range(1, len(divisors) + 1):
        for group in combinations(divisors, size):
            divisor = math.lcm(*group)
            count = (limit - 1) // divisor
            subtotal = divisor * count * (count + 1) // 2
            total += subtotal if size % 2 else -subtotal
    return total


def _legendre(n: int, prime: int) -> int:
    total = 0
    while n:
        n //= prime
        total += n
    return total


def _sum_powers(n: int, power: int) -> int:
    if power == 2:
        return n * (n + 1) * (2 * n + 1) // 6
    if power == 3:
        return (n * (n + 1) // 2) ** 2
    raise ValueError("unsupported power")


def _base7(n: int) -> str:
    digits = ""
    while n:
        n, digit = divmod(n, 7)
        digits = str(digit) + digits
    return digits or "0"


LESSONS = {
    "A": "Procedure: use inclusion-exclusion for overlapping divisibility sets and arithmetic-series sums.",
    "B": "Procedure: use Legendre's formula, repeatedly dividing the factorial input by the relevant prime and summing the quotients.",
    "C": "Procedure: encode shortest monotone grid paths as choices of move positions and evaluate a binomial coefficient.",
    "D": "Procedure: for a prime factorization, multiply each exponent increased by one to count positive divisors.",
    "E": "Procedure: apply the appropriate closed form for a sum of consecutive integer powers with exact arithmetic.",
    "F": "Procedure: use repeated squaring and reduce after every multiplication to evaluate a modular power.",
}


def expanded_cases() -> list[ReasoningCase]:
    """Return the frozen twenty-eight-case plan; answers execute at runtime."""
    specs: list[tuple[str, str, str, str, Callable[[], Union[int, str]]]] = [
        ("A", "dev", "easy", "Sum positive integers below 1000 divisible by 3 or 7.", lambda: _sum_multiples_below(1000, (3, 7))),
        ("A", "dev", "hard", "Sum positive integers below 100000000 divisible by 3, 5, or 7.", lambda: _sum_multiples_below(100_000_000, (3, 5, 7))),
        ("A", "holdout", "easy", "Sum positive integers below 5000 divisible by 4 or 6.", lambda: _sum_multiples_below(5000, (4, 6))),
        ("A", "holdout", "hard", "Sum positive integers below 100000000 divisible by 2, 3, or 11.", lambda: _sum_multiples_below(100_000_000, (2, 3, 11))),
        ("B", "dev", "easy", "Find the trailing zeroes in 1000 factorial.", lambda: _legendre(1000, 5)),
        ("B", "dev", "hard", "Find the exponent of 3 in the prime factorization of 250000 factorial.", lambda: _legendre(250_000, 3)),
        ("B", "holdout", "easy", "Find the trailing zeroes in 5000 factorial.", lambda: _legendre(5000, 5)),
        ("B", "holdout", "hard", "Find the exponent of 7 in the prime factorization of 1000000 factorial.", lambda: _legendre(1_000_000, 7)),
        ("C", "dev", "easy", "Count shortest right/down paths across a 10 by 10 grid.", lambda: math.comb(20, 10)),
        ("C", "dev", "hard", "Count shortest right/down paths across a 40 by 40 grid.", lambda: math.comb(80, 40)),
        ("C", "holdout", "easy", "Count shortest right/down paths across a 12 by 12 grid.", lambda: math.comb(24, 12)),
        ("C", "holdout", "hard", "Count shortest right/down paths across a 25 by 35 grid.", lambda: math.comb(60, 25)),
        ("D", "dev", "easy", "Count positive divisors of 2^5 * 3^2.", lambda: 6 * 3),
        ("D", "dev", "hard", "Count positive divisors of 2^10 * 3^6 * 5^4 * 7^3 * 11^2.", lambda: 11 * 7 * 5 * 4 * 3),
        ("D", "holdout", "easy", "Count positive divisors of 2^4 * 3^3 * 5^2.", lambda: 5 * 4 * 3),
        ("D", "holdout", "hard", "Count positive divisors of 2^15 * 3^9 * 5^6 * 7^2.", lambda: 16 * 10 * 7 * 3),
        ("E", "dev", "easy", "Sum the squares from 1 through 1000.", lambda: _sum_powers(1000, 2)),
        ("E", "dev", "hard", "Sum the cubes from 1 through 200000.", lambda: _sum_powers(200_000, 3)),
        ("E", "holdout", "easy", "Sum the squares from 1 through 2000.", lambda: _sum_powers(2000, 2)),
        ("E", "holdout", "hard", "Sum the cubes from 1 through 300000.", lambda: _sum_powers(300_000, 3)),
        ("F", "dev", "easy", "Compute 3^1000 modulo 101.", lambda: pow(3, 1000, 101)),
        ("F", "dev", "hard", "Compute 11^54321 modulo 1000033.", lambda: pow(11, 54321, 1_000_033)),
        ("F", "holdout", "easy", "Compute 5^2024 modulo 10007.", lambda: pow(5, 2024, 10007)),
        ("F", "holdout", "hard", "Compute 13^87654 modulo 999983.", lambda: pow(13, 87654, 999983)),
        ("G", "dev", "easy", "Convert the Roman numeral MMXXVI to an integer.", lambda: 2026),
        ("G", "dev", "hard", "Determine occurrences of the specified character 'r' in the specified string 'transferable reasoning'.", lambda: "transferable reasoning".count("r")),
        ("G", "holdout", "easy", "Give the ISO weekday number 100 days after 2026-07-18.", lambda: (date(2026, 7, 18) + timedelta(days=100)).isoweekday()),
        ("G", "holdout", "hard", "Write 2026 using base-7 digits.", lambda: _base7(2026)),
    ]
    cases = [_case(f, s, d, task, op, LESSONS.get(f)) for f, s, d, task, op in specs]
    validate_catalog(cases)
    return cases


def seed_lessons(cases: list[ReasoningCase]) -> dict[str, str]:
    result: dict[str, str] = {}
    for case in cases:
        if case.family in LESSONS and case.split == "dev" and case.family not in result:
            if case.lesson is None:
                raise ValueError("procedural seed is missing a lesson")
            result[case.family] = case.lesson
    if set(result) != set(LESSONS):
        raise ValueError("one dev seed lesson is required for every procedural family")
    return result


def validate_catalog(cases: list[ReasoningCase]) -> None:
    ids = [case.case_id for case in cases]
    expected_strings = {case.expected for case in cases}
    for case in cases:
        if case.split not in {"dev", "holdout"}:
            raise ValueError(f"malformed split: {case.split}")
        if case.difficulty not in {"easy", "hard"}:
            raise ValueError(f"malformed difficulty: {case.difficulty}")
        if case.family not in set("ABCDEFG"):
            raise ValueError(f"malformed family: {case.family}")
        if case.expected != case.answer_fn():
            raise ValueError(f"runtime answer mismatch for {case.case_id}")
        if case.lesson and (any(case_id in case.lesson for case_id in ids)
                            or any(expected in case.lesson for expected in expected_strings)):
            raise ValueError(f"lesson leakage for {case.case_id}")
        if case.expects_injection != (case.family != "G"):
            raise ValueError(f"injection relation mismatch for {case.case_id}")
    required = {(family, split, difficulty) for family in "ABCDEFG"
                for split in ("dev", "holdout") for difficulty in ("easy", "hard")}
    matrix = [(case.family, case.split, case.difficulty) for case in cases]
    if len(ids) != len(set(ids)) or len(matrix) != len(set(matrix)):
        raise ValueError("duplicate case_id or matrix entry")
    if len(cases) != 28 or set(matrix) != required:
        raise ValueError("catalog must contain the exact 28-case family/split/difficulty matrix")
