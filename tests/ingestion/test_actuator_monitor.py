from __future__ import annotations

import pytest

from horizon_fusion.actuator import assess_actuator_response


def command_observation(*, command_id: str = "command-1", requested: float = 0.4) -> dict:
    return {
        "contract_type": "Observation",
        "observation_id": "setpoint-observation",
        "input_group": "internal_ship_communications",
        "source_id": "actuator/setpoint",
        "sequence": 0,
        "time": {"event_time_s": 10.0},
        "payload": {
            "message_type": "actuator_setpoint",
            "command_id": command_id,
            "commanded_rudder_rad": requested,
        },
    }


def feedback_observation(sequence: int, actual: float, *, linked: bool = True) -> dict:
    ancestors = ["setpoint-observation"] if linked else ["unrelated-observation"]
    return {
        "contract_type": "Observation",
        "observation_id": f"feedback-{sequence}",
        "input_group": "ship_actuator_feedback",
        "source_id": "actuator",
        "sequence": sequence,
        "time": {"event_time_s": 10.0 + sequence * 0.25},
        "payload": {
            "applied_command_id": "command-1",
            "rudder_rad": actual,
            "_collector": {"ancestor_ids": ancestors},
        },
    }


def test_matching_or_sparse_samples_never_promote_configured_limits() -> None:
    observations = [command_observation()] + [
        feedback_observation(sequence, 0.1 * sequence) for sequence in range(3)
    ]
    result = assess_actuator_response(
        observations,
        configured_rate_rps=0.5,
        configured_lag_s=0.4,
    )
    assert result.status == "degraded"
    assert result.rudder_rate_limit_rps == 0.5
    assert "linked_response_samples_insufficient" in result.reasons


def test_unlinked_feedback_is_not_treated_as_command_response_evidence() -> None:
    observations = [command_observation()] + [
        feedback_observation(sequence, 0.0, linked=False) for sequence in range(6)
    ]
    result = assess_actuator_response(
        observations,
        configured_rate_rps=0.5,
        configured_lag_s=0.4,
    )
    assert result.evidence["status"] == "unavailable"
    assert result.reasons == (
        "configured_limits_only",
        "online_capability_degradation_state_unavailable",
    )


@pytest.mark.parametrize(
    ("actuals", "expected_reason", "maximum_rate"),
    [
        ([0.0] * 6, "observed_rudder_response_stuck", 1e-6),
        ([0.0, 0.01, 0.02, 0.03, 0.04, 0.05], "observed_rudder_rate_below_configured", 0.05),
    ],
)
def test_sustained_linked_step_restricts_stuck_or_slow_rudder(
    actuals: list[float], expected_reason: str, maximum_rate: float
) -> None:
    observations = [command_observation()] + [
        feedback_observation(sequence, actual) for sequence, actual in enumerate(actuals)
    ]
    result = assess_actuator_response(
        observations,
        configured_rate_rps=0.5,
        configured_lag_s=0.4,
    )
    assert result.status == "degraded"
    assert result.rudder_rate_limit_rps <= maximum_rate
    assert expected_reason in result.reasons
    assert result.evidence["feedback_observation_ids"] == [
        f"feedback-{sequence}" for sequence in range(6)
    ]
