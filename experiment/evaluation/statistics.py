from __future__ import annotations

import math
from statistics import NormalDist
from typing import Iterable


def percentile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    if not 0 <= probability <= 1:
        raise ValueError("probability must be in [0, 1]")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def runtime_summary(runtime_ns: Iterable[int]) -> dict[str, float | int]:
    values = list(runtime_ns)
    if not values:
        return {"count": 0, "p50_ns": 0.0, "p95_ns": 0.0, "p99_ns": 0.0, "max_ns": 0}
    return {
        "count": len(values),
        "p50_ns": percentile(values, 0.50),
        "p95_ns": percentile(values, 0.95),
        "p99_ns": percentile(values, 0.99),
        "max_ns": max(values),
    }


def wilson_interval(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError("require 0 <= successes <= trials and trials > 0")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    observed = successes / trials
    denominator = 1 + z * z / trials
    center = (observed + z * z / (2 * trials)) / denominator
    half_width = z * math.sqrt(
        observed * (1 - observed) / trials + z * z / (4 * trials * trials)
    ) / denominator
    return max(0.0, center - half_width), min(1.0, center + half_width)


def exact_mcnemar_pvalue(a_unsafe: Iterable[bool], b_unsafe: Iterable[bool]) -> float:
    pairs = list(zip(a_unsafe, b_unsafe, strict=True))
    a_only = sum(a and not b for a, b in pairs)
    b_only = sum(b and not a for a, b in pairs)
    discordant = a_only + b_only
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, k) for k in range(0, min(a_only, b_only) + 1))
    return min(1.0, 2.0 * tail / (2**discordant))
