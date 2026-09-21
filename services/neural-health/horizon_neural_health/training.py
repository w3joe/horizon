"""Offline fitting utilities with strict split and provenance checks."""

from __future__ import annotations

import math
from typing import Any

from .artifact import canonical_hash
from .artifact import REQUIRED_PROVENANCE, require_finite
from .models import fit_h2, fit_h3, fit_h4


def build_reference(
    method_id: str,
    rows: list[list[float]],
    layer: str,
    source_groups: list[str],
    version: str,
    fit_split: str = "nominal_reference",
    intervention_validation: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    **options: Any,
) -> dict[str, Any]:
    if fit_split not in {"development", "nominal_reference"}:
        raise ValueError("references may use development/nominal_reference data only")
    if not source_groups:
        raise ValueError("source provenance groups are required")
    if not isinstance(provenance, dict) or not REQUIRED_PROVENANCE.issubset(provenance):
        raise ValueError("complete reference provenance is required")
    if provenance["layer"] != layer or list(provenance["source_groups"]) != source_groups:
        raise ValueError("reference provenance does not match layer/source groups")
    if method_id == "H2":
        parameters = fit_h2(rows, float(options.get("regularization", 1e-3)))
    elif method_id == "H3":
        parameters = fit_h3(rows, int(options.get("components", min(8, len(rows[0])))))
    elif method_id == "H4":
        parameters = fit_h4(
            rows,
            hidden=int(options.get("hidden", min(8, len(rows[0])))),
            epochs=int(options.get("epochs", 80)),
            learning_rate=float(options.get("learning_rate", 0.01)),
            l1=float(options.get("l1", 1e-3)),
            seed=int(options.get("seed", 0)),
            tolerance=float(options.get("tolerance", 1e-7)),
        )
        parameters["offline_intervention_validation"] = intervention_validation or {
            "completed_controls": False,
            "status": "pending",
        }
    else:
        raise ValueError("reference artifacts exist only for H2-H4")
    body = {
        "method_id": method_id,
        "version": version,
        "layer": layer,
        "feature_dimension": len(rows[0]),
        "fit_split": fit_split,
        "source_groups": source_groups,
        "parameters": parameters,
        "provenance": provenance,
    }
    require_finite(body)
    return {**body, "artifact_hash": canonical_hash(body)}


def build_calibration(
    method_id: str,
    samples: list[dict[str, Any]],
    scope: dict[str, Any],
    version: str,
    max_false_alarm_rate: float,
    reference_hash: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fit a threshold and empirical miss-risk bands using calibration labels."""
    if not 0 <= max_false_alarm_rate < 1:
        raise ValueError("max_false_alarm_rate must be in [0, 1)")
    benign = sorted(float(row["score"]) for row in samples if not bool(row["missed_obstacle"]))
    fault = [float(row["score"]) for row in samples if bool(row["missed_obstacle"])]
    if not benign or not fault:
        raise ValueError("calibration requires benign and missed-obstacle samples")
    if not isinstance(provenance, dict) or not REQUIRED_PROVENANCE.issubset(provenance):
        raise ValueError("complete calibration provenance is required")
    require_finite(samples)
    allowed_false = math.floor(max_false_alarm_rate * len(benign))
    candidates = sorted({*benign, *fault, math.nextafter(max(benign + fault), math.inf)})
    valid = [candidate for candidate in candidates if sum(score >= candidate for score in benign) <= allowed_false]
    threshold = valid[0]
    all_scores = sorted(float(row["score"]) for row in samples)
    edges = [
        math.nextafter(all_scores[0], -math.inf),
        all_scores[len(all_scores) // 3],
        all_scores[2 * len(all_scores) // 3],
        math.nextafter(all_scores[-1], math.inf),
    ]
    bins = []
    for index, (lower, upper) in enumerate(zip(edges, edges[1:])):
        members = [row for row in samples if lower <= float(row["score"]) < upper]
        if not members:
            continue
        misses = sum(bool(row["missed_obstacle"]) for row in members)
        rate = misses / len(members)
        interval = _wilson(misses, len(members))
        bins.append({
            "lower": lower,
            "upper": upper,
            "label": f"calibration_score_bin_{index + 1}",
            "miss_count": misses,
            "empirical_rate": rate,
            "interval": list(interval),
            "samples": len(members),
        })
    body = {
        "method_id": method_id,
        "version": version,
        "reference_hash": reference_hash,
        "alarm_threshold": threshold,
        "scope": scope,
        "risk_bins": bins,
        "sample_count": len(samples),
        "risk_validated_on_heldout": False,
        "provenance": provenance,
        "split": "calibration",
        "frozen": True,
    }
    return {**body, "artifact_hash": canonical_hash(body)}


def _wilson(successes: int, count: int, z: float = 1.96) -> tuple[float, float]:
    center = (successes + z * z / 2) / (count + z * z)
    half = z * math.sqrt(successes * (count - successes) / count + z * z / 4) / (count + z * z)
    return max(0.0, center - half), min(1.0, center + half)
