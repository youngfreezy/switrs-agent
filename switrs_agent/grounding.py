"""Deterministic checks that an answer is backed by query results.

verify() extracts every number from the answer text and requires each one to be either a value
in a cited query result, or a simple derivation of such values (ratio, percent, percent change,
difference, share of a column total). Numbers the user wrote in the question and calendar years
are exempt.
"""

from __future__ import annotations

import bisect
import itertools
import re
from dataclasses import dataclass

NUM = re.compile(
    r"(?<![\w.])((?:[+\-−]\s*)?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:e[+\-−]?\d+)?)"
    r"\s*(%|x\b|times\b|percent\b|trillion\b|billion\b|bn\b|b\b|million\b|m\b|k\b|thousand\b)?",
    re.IGNORECASE,
)
DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")
QUERY_ID = re.compile(r"\b[0-9a-f]{4}-q\d+\b|\bq\d+\b", re.IGNORECASE)  # "7328-q1" is a citation, not a claim
MAX_PAIR_VALUES = 400


@dataclass
class Claim:
    text: str
    value: float
    decimals: int
    kind: str  # plain | percent | ratio


def extract_numbers(text: str) -> list[Claim]:
    text = DATE.sub(" ", QUERY_ID.sub(" ", text))
    out = []
    for m in NUM.finditer(text):
        raw, unit = m.group(1), (m.group(2) or "").lower()
        raw_value = re.sub(r"\s+", "", raw).replace(",", "").replace("−", "-").lower()
        value = float(raw_value)
        mantissa, _, exponent = raw_value.partition("e")
        if len(exponent.lstrip("+-").lstrip("0")) > 3:
            raise ValueError("Scientific exponent is outside the supported range.")
        decimals = (len(mantissa.split(".")[1]) if "." in mantissa else 0) - int(exponent or 0)
        kind = "plain"
        if unit in ("%", "percent"):
            kind = "percent"
        elif unit in ("x", "times"):
            kind = "ratio"
        elif unit in ("million", "m"):
            value *= 1_000_000
            decimals -= 6
        elif unit in ("billion", "bn", "b", "trillion"):
            scale = 12 if unit == "trillion" else 9
            value *= 10 ** scale
            decimals -= scale
        elif unit in ("k", "thousand"):
            value *= 1_000
            decimals -= 3
        out.append(Claim(m.group(0).strip(), value, decimals, kind))
    return out


def _is_exempt(c: Claim, question_numbers: set[float]) -> bool:
    if c.kind == "plain" and c.decimals == 0 and 1990 <= c.value <= 2030:
        return True  # calendar year used as a label
    if c.kind == "plain" and c.decimals == 0 and 0 <= c.value <= 10:
        return True  # small counts and ordinals ("top 3", "one of two definitions")
    return c.value in question_numbers


def _candidates(results: list[dict]) -> tuple[set[float], set[float]]:
    """Return (values, derived) from result tables."""
    values: set[float] = set()
    columns: dict[tuple[int, int], list[float]] = {}
    rows_numeric: list[list[float]] = []
    for qi, r in enumerate(results):
        for row in r["rows"]:
            nums = []
            for ci, v in enumerate(row):
                if isinstance(v, bool) or v is None:
                    continue
                if isinstance(v, (int, float)):
                    f = float(v)
                elif isinstance(v, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", v):
                    f = float(v)
                else:
                    continue
                values.add(f)
                nums.append(f)
                columns.setdefault((qi, ci), []).append(f)
            rows_numeric.append(nums)

    derived: set[float] = set()

    def add_pair(a: float, b: float) -> None:
        derived.add(a - b)
        if b:
            derived.add(a / b)
            derived.add(100 * a / b)
            derived.add(100 * (a - b) / b)

    for nums in rows_numeric:
        for a, b in itertools.permutations(nums, 2):
            add_pair(a, b)
    for col in columns.values():
        total = sum(col)
        derived.add(total)
        for v in col:
            add_pair(v, total)
        if len(col) <= 60:
            for a, b in itertools.permutations(col, 2):
                add_pair(a, b)
    flat = sorted(values)
    if len(flat) <= MAX_PAIR_VALUES:
        for a, b in itertools.permutations(flat, 2):
            add_pair(a, b)
    return values, derived


def _within(sorted_pool: list[float], target: float, tol: float) -> bool:
    i = bisect.bisect_left(sorted_pool, target - tol)
    return i < len(sorted_pool) and sorted_pool[i] <= target + tol


def _matches(c: Claim, sorted_pool: list[float]) -> bool:
    tol = max(0.5 * 10 ** (-c.decimals), 1e-9) + 1e-9
    for t in (c.value, -c.value):
        if _within(sorted_pool, t, tol):
            return True
        # the result is a fraction and the answer shows it as a percent
        if c.kind == "percent" and _within(sorted_pool, t / 100, tol / 100):
            return True
    return False


def verify(answer: str, results: list[dict], question: str = "") -> dict:
    claims = extract_numbers(answer)
    q_numbers = {c.value for c in extract_numbers(question)}
    values, derived = _candidates(results)
    pool = sorted(values | derived)
    unsupported, supported = [], 0
    for c in claims:
        if _is_exempt(c, q_numbers) or _matches(c, pool):
            supported += 1
        else:
            unsupported.append(c.text)
    return {
        "ok": not unsupported,
        "numbers_checked": len(claims),
        "numbers_supported": supported,
        "unsupported_numbers": unsupported,
    }
