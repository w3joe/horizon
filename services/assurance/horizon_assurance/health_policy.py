"""Operating-mode source qualification shared by supervisor and actuator gate."""

from __future__ import annotations

from typing import Any

from .configuration import AssuranceConfig


def required_health_evidence(
    governor_input: dict[str, Any],
    config: AssuranceConfig,
    *,
    recovery: bool = False,
    now_ns: int | None = None,
) -> tuple[int, tuple[str, ...]]:
    """Return the original evidence expiry and any unavailable-source reasons.

    Independent geometric recovery needs navigation/perception/actuator data,
    but does not require the primary AI to be alive. Degraded evidence can be
    used only under the controller's separate bounded-capability policy.
    """
    recovery_exempt = {"decision_ai_telemetry", "internal_ship_communications"}
    if config.camera_reliance_mode == "recorded_camera_supporting":
        recovery_exempt.add(config.camera_health_source)
    required = tuple(
        source for source in config.required_health_sources
        if not recovery or source not in recovery_exempt
    )
    current = int(governor_input["monotonic_time_ns"]) if now_ns is None else now_ns
    expiry = int(governor_input["snapshot"]["valid_until_monotonic_ns"])
    reasons: list[str] = []
    if config.camera_reliance_mode == "recorded_camera_supporting" and not recovery:
        health_id = governor_input["health"].get("perception_health_id")
        if not isinstance(health_id, str) or not health_id:
            reasons.append("CAMERA_RELIANCE_HEALTH_ID_MISSING")
    summaries = governor_input["health"].get("summaries", [])
    for source in required:
        matches = [item for item in summaries if item["source_id"] == source]
        if len(matches) != 1:
            reasons.append(f"REQUIRED_HEALTH_SOURCE_MISSING_OR_DUPLICATE:{source}")
            expiry = min(expiry, current)
            continue
        item = matches[0]
        source_expiry = int(item["valid_until_monotonic_ns"])
        expiry = min(expiry, source_expiry)
        if (
            item["status"] in {"unknown", "invalid"}
            or item["capability"] in {"unavailable", "output_only"}
            or source_expiry <= current
        ):
            reasons.append(f"REQUIRED_HEALTH_SOURCE_UNAVAILABLE:{source}")
    return expiry, tuple(reasons)
