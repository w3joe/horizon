from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ThresholdResult:
    threshold: float
    false_alarm_rate: float
    detection_rate: float
    benign_count: int
    fault_count: int


def calibrate_threshold(
    scores_and_faults: Iterable[tuple[float, bool]], max_false_alarm_rate: float
) -> ThresholdResult:
    """Select a high-score-is-risk threshold using calibration data only.

    The threshold maximizes fault detection subject to the empirical false-alarm
    budget. Ties choose the higher threshold, yielding the more conservative
    alarm count. This is empirical calibration, not a distribution-free bound.
    """
    if not 0 <= max_false_alarm_rate <= 1:
        raise ValueError("max_false_alarm_rate must be in [0, 1]")
    samples = [(float(score), bool(is_fault)) for score, is_fault in scores_and_faults]
    benign = [score for score, is_fault in samples if not is_fault]
    faults = [score for score, is_fault in samples if is_fault]
    if not benign or not faults:
        raise ValueError("calibration requires benign and fault samples")
    no_alarm_threshold = math.nextafter(max(score for score, _ in samples), math.inf)
    candidates = [no_alarm_threshold, *sorted({score for score, _ in samples}, reverse=True)]
    feasible: list[ThresholdResult] = []
    for threshold in candidates:
        false_alarm_rate = sum(score >= threshold for score in benign) / len(benign)
        detection_rate = sum(score >= threshold for score in faults) / len(faults)
        if false_alarm_rate <= max_false_alarm_rate:
            feasible.append(
                ThresholdResult(
                    threshold, false_alarm_rate, detection_rate, len(benign), len(faults)
                )
            )
    return max(feasible, key=lambda result: (result.detection_rate, result.threshold))


def require_calibration_split(split: str) -> None:
    if split != "calibration":
        raise ValueError("threshold fitting is permitted only on the calibration split")
