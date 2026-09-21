"""Health record kept deliberately independent of shared contract generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    INVALID = "invalid"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PerceptionHealth:
    sensor_id: str
    inference_id: str
    frame_ids: tuple[str, ...]
    valid_until_ns: int
    status: HealthStatus
    reasons: tuple[str, ...]
    method_id: str
    reference_model_version: str
    calibration_version: str | None
    capability: str
    completeness: str
    camera_free_space_usable: bool
    missed_obstacle_risk: dict[str, Any]
    risk_scope: dict[str, Any] | None
    geometric_uncertainty: dict[str, Any]
    supporting_observation_ids: tuple[str, ...]
    statistics: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        result["frame_ids"] = list(self.frame_ids)
        result["reasons"] = list(self.reasons)
        result["supporting_observation_ids"] = list(self.supporting_observation_ids)
        return result


def unknown_record(payload: dict[str, Any], method_id: str, reason: str) -> PerceptionHealth:
    return PerceptionHealth(
        sensor_id=str(payload.get("sensor_id", "unknown")),
        inference_id=str(payload.get("inference_id", "unknown")),
        frame_ids=tuple(str(x) for x in payload.get("frame_ids", [])),
        valid_until_ns=int(payload.get("valid_until_ns", 0)),
        status=HealthStatus.UNKNOWN,
        reasons=(reason,),
        method_id=method_id,
        reference_model_version=str(payload.get("reference_model_version", "unknown")),
        calibration_version=None,
        capability="unavailable",
        completeness="missing_required_evidence",
        camera_free_space_usable=False,
        missed_obstacle_risk={"kind": "unknown"},
        risk_scope=None,
        geometric_uncertainty={"kind": "unknown"},
        supporting_observation_ids=tuple(str(x) for x in payload.get("observation_ids", [])),
        statistics={},
    )
