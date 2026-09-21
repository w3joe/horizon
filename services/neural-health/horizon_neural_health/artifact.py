"""Versioned reference, calibration, and cached-feature artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable
import math


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def require_finite(value: Any, path: str = "artifact") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a nonfinite number")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            require_finite(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            require_finite(item, f"{path}[{index}]")
        return
    raise ValueError(f"{path} contains unsupported value {type(value).__name__}")


REQUIRED_PROVENANCE = {
    "model_weights_sha256",
    "preprocessing_sha256",
    "sensor_geometry_version",
    "layer",
    "input_dimension",
    "projection",
    "source_groups",
}


@dataclass(frozen=True)
class ReferenceArtifact:
    method_id: str
    version: str
    layer: str
    feature_dimension: int
    fit_split: str
    source_groups: tuple[str, ...]
    parameters: dict[str, Any]
    provenance: dict[str, Any]
    artifact_hash: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReferenceArtifact":
        body = {key: val for key, val in value.items() if key != "artifact_hash"}
        require_finite(body)
        expected = canonical_hash(body)
        if value.get("artifact_hash") != expected:
            raise ValueError("reference artifact hash mismatch")
        if value.get("fit_split") not in {"development", "nominal_reference"}:
            raise ValueError("reference fit split must not be calibration or heldout")
        provenance = value.get("provenance")
        if not isinstance(provenance, dict) or not REQUIRED_PROVENANCE.issubset(provenance):
            raise ValueError("reference artifact provenance is incomplete")
        if provenance["layer"] != value["layer"]:
            raise ValueError("reference layer provenance mismatch")
        if int(provenance["projection"]["output_dimension"]) != int(value["feature_dimension"]):
            raise ValueError("reference projection dimension mismatch")
        return cls(
            method_id=value["method_id"],
            version=value["version"],
            layer=value["layer"],
            feature_dimension=int(value["feature_dimension"]),
            fit_split=value["fit_split"],
            source_groups=tuple(value["source_groups"]),
            parameters=value["parameters"],
            provenance=provenance,
            artifact_hash=value["artifact_hash"],
        )

    @classmethod
    def load(cls, path: str | Path) -> "ReferenceArtifact":
        return cls.from_dict(json.loads(Path(path).read_text()))


@dataclass(frozen=True)
class CalibrationArtifact:
    method_id: str
    version: str
    reference_hash: str | None
    alarm_threshold: float
    scope: dict[str, Any]
    risk_bins: tuple[dict[str, Any], ...]
    sample_count: int
    risk_validated_on_heldout: bool
    provenance: dict[str, Any]
    artifact_hash: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CalibrationArtifact":
        body = {key: val for key, val in value.items() if key != "artifact_hash"}
        require_finite(body)
        if canonical_hash(body) != value.get("artifact_hash"):
            raise ValueError("calibration artifact hash mismatch")
        if value.get("split") != "calibration" or not value.get("frozen", False):
            raise ValueError("calibration artifact must be frozen and calibration-only")
        provenance = value.get("provenance")
        if not isinstance(provenance, dict) or not REQUIRED_PROVENANCE.issubset(provenance):
            raise ValueError("calibration artifact provenance is incomplete")
        return cls(
            method_id=value["method_id"],
            version=value["version"],
            reference_hash=value.get("reference_hash"),
            alarm_threshold=float(value["alarm_threshold"]),
            scope=value["scope"],
            risk_bins=tuple(value.get("risk_bins", [])),
            sample_count=int(value["sample_count"]),
            risk_validated_on_heldout=bool(value.get("risk_validated_on_heldout", False)),
            provenance=provenance,
            artifact_hash=value["artifact_hash"],
        )

    @classmethod
    def load(cls, path: str | Path) -> "CalibrationArtifact":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def applies(self, context: dict[str, Any]) -> bool:
        return all(context.get(key) in allowed for key, allowed in self.scope.items())

    def risk(self, score: float, context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if not self.applies(context):
            return {"kind": "unknown", "reason": "outside_calibrated_scope"}, None
        if not self.risk_validated_on_heldout:
            return {"kind": "unknown", "reason": "risk_band_not_heldout_validated"}, self.scope
        for bucket in self.risk_bins:
            if float(bucket["lower"]) <= score < float(bucket["upper"]):
                return {
                    "kind": "calibrated_band",
                    "label": bucket["label"],
                    "empirical_rate": bucket["empirical_rate"],
                    "interval": bucket["interval"],
                    "samples": bucket["samples"],
                }, self.scope
        return {"kind": "unknown", "reason": "score_outside_calibrated_bins"}, self.scope


class FeatureCache:
    """Read real pooled hook features while retaining frame/layer provenance."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def rows(self, layer: str, dimensions: int | None = 64) -> Iterable[tuple[dict[str, Any], list[float]]]:
        with self.path.open() as stream:
            for line in stream:
                row = json.loads(line)
                summary = row.get("activation_summaries", {}).get(layer)
                if summary is None:
                    continue
                feature = [*summary["pooled_mean"], *summary["pooled_standard_deviation"]]
                vector = [float(value) for value in feature]
                yield row, project_groups(vector, dimensions) if dimensions else vector


def project_groups(vector: list[float], dimensions: int) -> list[float]:
    """Deterministic fixed grouped projection recorded by dimension in artifacts."""
    if dimensions <= 0 or dimensions > len(vector):
        raise ValueError("projected dimensions must be in (0, input dimension]")
    result = []
    for group in range(dimensions):
        start = group * len(vector) // dimensions
        end = (group + 1) * len(vector) // dimensions
        result.append(sum(vector[start:end]) / (end - start))
    return result
