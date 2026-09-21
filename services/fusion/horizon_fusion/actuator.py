from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable


@dataclass(frozen=True)
class ActuatorAssessment:
    rudder_rate_limit_rps: float
    steering_lag_s: float
    status: str
    reasons: tuple[str, ...]
    evidence: dict[str, Any]


def _collector_ancestors(observation: dict[str, Any]) -> set[str]:
    collector = observation.get("payload", {}).get("_collector", {})
    values = collector.get("ancestor_ids", [])
    return {str(value) for value in values if isinstance(value, str)}


def assess_actuator_response(
    observations: Iterable[dict[str, Any]],
    *,
    configured_rate_rps: float,
    configured_lag_s: float,
) -> ActuatorAssessment:
    """Conservatively restrict configured steering capability from linked response evidence.

    A feedback sample is usable only when it names the command and carries the
    command observation in its collector ancestry (or names that observation
    explicitly). This prevents temporal coincidence from being treated as a
    command/response measurement. Successful response samples do not validate a
    vessel-wide capability bound, so this monitor never returns ``nominal``.
    """

    baseline_reasons = (
        "configured_limits_only",
        "online_capability_degradation_state_unavailable",
    )
    commands: dict[str, dict[str, Any]] = {}
    feedback: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        if observation.get("contract_type") != "Observation":
            continue
        payload = observation.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if (
            observation.get("input_group") == "internal_ship_communications"
            and payload.get("message_type") == "actuator_setpoint"
        ):
            command_id = payload.get("command_id")
            requested = payload.get("commanded_rudder_rad")
            if isinstance(command_id, str) and command_id and isinstance(requested, (int, float)):
                requested_value = float(requested)
                if math.isfinite(requested_value):
                    commands[command_id] = observation
        if observation.get("source_id") != "actuator":
            continue
        command_id = payload.get("applied_command_id")
        actual = payload.get("rudder_rad")
        if isinstance(command_id, str) and command_id and isinstance(actual, (int, float)):
            actual_value = float(actual)
            if math.isfinite(actual_value):
                feedback.setdefault(command_id, []).append(observation)

    candidates: list[tuple[float, str, dict[str, Any], list[dict[str, Any]]]] = []
    for command_id, samples in feedback.items():
        command = commands.get(command_id)
        if command is None:
            continue
        command_observation_id = str(command.get("observation_id", ""))
        linked = [
            sample
            for sample in samples
            if command_observation_id in _collector_ancestors(sample)
            or sample.get("payload", {}).get("command_observation_id") == command_observation_id
        ]
        if not linked:
            continue
        event_time = float(command["time"]["event_time_s"])
        candidates.append((event_time, command_id, command, linked))

    if not candidates:
        return ActuatorAssessment(
            configured_rate_rps,
            configured_lag_s,
            "degraded",
            baseline_reasons,
            {"status": "unavailable", "reason": "no_lineage_linked_command_response"},
        )

    _, command_id, command, samples = max(candidates, key=lambda item: item[0])
    samples.sort(key=lambda item: (float(item["time"]["event_time_s"]), int(item["sequence"])))
    command_time = float(command["time"]["event_time_s"])
    samples = [
        sample for sample in samples if float(sample["time"]["event_time_s"]) >= command_time
    ]
    sample_ids = [str(sample["observation_id"]) for sample in samples]
    evidence: dict[str, Any] = {
        "status": "insufficient",
        "command_id": command_id,
        "command_observation_id": str(command["observation_id"]),
        "feedback_observation_ids": sample_ids,
        "sample_count": len(samples),
    }
    if len(samples) < 5:
        return ActuatorAssessment(
            configured_rate_rps,
            configured_lag_s,
            "degraded",
            baseline_reasons + ("linked_response_samples_insufficient",),
            evidence,
        )

    times = [float(sample["time"]["event_time_s"]) for sample in samples]
    actuals = [float(sample["payload"]["rudder_rad"]) for sample in samples]
    requested = float(command["payload"]["commanded_rudder_rad"])
    span_s = times[-1] - times[0]
    excitation_rad = abs(requested - actuals[0])
    evidence.update({"span_s": span_s, "command_excitation_rad": excitation_rad})
    if span_s < 0.5 or excitation_rad < 0.1:
        return ActuatorAssessment(
            configured_rate_rps,
            configured_lag_s,
            "degraded",
            baseline_reasons + ("command_excitation_or_span_insufficient",),
            evidence,
        )

    rates = [
        abs(second_actual - first_actual) / (second_time - first_time)
        for first_time, second_time, first_actual, second_actual in zip(
            times, times[1:], actuals, actuals[1:]
        )
        if second_time > first_time
    ]
    peak_rate = max(rates, default=0.0)
    displacement = max(abs(actual - actuals[0]) for actual in actuals)
    movement_threshold = min(0.02, excitation_rad * 0.2)
    moving_times = [
        sample_time
        for sample_time, actual in zip(times, actuals)
        if abs(actual - actuals[0]) >= movement_threshold
    ]
    observed_lag = max(0.0, min(moving_times) - command_time) if moving_times else span_s
    evidence.update(
        {
            "status": "assessed",
            "observed_peak_rudder_rate_rps": peak_rate,
            "observed_rudder_displacement_rad": displacement,
            "observed_response_lag_s": observed_lag,
        }
    )

    reasons = ["configured_limits_not_vessel_characterized", "linked_command_feedback_evidence"]
    conservative_rate = configured_rate_rps
    conservative_lag = configured_lag_s
    if span_s >= 1.0 and displacement < 0.02:
        conservative_rate = max(1e-6, min(configured_rate_rps, peak_rate or 1e-6))
        conservative_lag = max(configured_lag_s, span_s)
        reasons.append("observed_rudder_response_stuck")
    elif peak_rate < configured_rate_rps * 0.5:
        conservative_rate = max(1e-6, min(configured_rate_rps, peak_rate or 1e-6))
        conservative_lag = max(configured_lag_s, observed_lag)
        reasons.append("observed_rudder_rate_below_configured")
    else:
        reasons.append("response_observed_but_bound_not_validated")
    if observed_lag > max(configured_lag_s * 2.0, configured_lag_s + 0.25):
        conservative_lag = max(conservative_lag, observed_lag)
        reasons.append("observed_steering_lag_above_configured")
    return ActuatorAssessment(
        conservative_rate,
        conservative_lag,
        "degraded",
        tuple(reasons),
        evidence,
    )
