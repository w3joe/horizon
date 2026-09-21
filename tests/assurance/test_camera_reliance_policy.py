from __future__ import annotations

import copy
from dataclasses import replace
import time

import pytest

from horizon_assurance.candidates import A1ThresholdSimplex
from horizon_assurance.configuration import AssuranceConfig
from horizon_assurance.health_policy import required_health_evidence
from horizon_gate.core import ActuatorGate, GateConfig


class RecordingPlant:
    def __init__(self) -> None:
        self.envelopes: list[dict] = []

    def command(self, envelope: dict) -> dict:
        self.envelopes.append(copy.deepcopy(envelope))
        now = time.monotonic_ns()
        return {
            "contract_type": "GateReceipt",
            "schema_version": "0.1.0",
            "receipt_id": f"camera-policy-receipt:{len(self.envelopes)}",
            "run_id": envelope["run_id"],
            "branch_id": envelope["branch_id"],
            "decision_id": envelope["decision_id"],
            "command_id": envelope["command_id"],
            "authority": envelope["authority"],
            "accepted": True,
            "reason_codes": [],
            "received_monotonic_ns": now,
            "actuated_monotonic_ns": now,
            "actual_command": envelope["command"],
        }

    def snapshot(self) -> dict:
        return {"simulation_time_s": 4.2}


def _retime(message: dict) -> dict:
    value = copy.deepcopy(message)
    now = time.monotonic_ns()
    value["monotonic_time_ns"] = now
    value["decision_deadline_monotonic_ns"] = now + 2_000_000_000
    value["snapshot"]["valid_until_monotonic_ns"] = now + 3_000_000_000
    value["proposal"]["issued_monotonic_ns"] = now
    value["proposal"]["expires_monotonic_ns"] = now + 3_000_000_000
    for option in value["recovery_options"]:
        option["valid_until_monotonic_ns"] = now + 3_000_000_000
    for summary in value["health"]["summaries"]:
        summary["valid_until_monotonic_ns"] = now + 3_000_000_000
    return value


def _gate(reference, plant: RecordingPlant, config: AssuranceConfig) -> ActuatorGate:
    return ActuatorGate(
        run_id="fixture-run-001",
        branch_id="protected",
        plant=plant,
        reference=reference,
        decision_token="decision-secret",
        recovery_token="recovery-secret",
        operator_token="operator-secret",
        config=GateConfig(startup_interlock_required=False, asynchronous_recovery_cache=False),
        assurance_config=config,
    )


def test_configured_camera_reliance_changes_health_without_relaxing_recovery_sources(
    reference, governor_input
) -> None:
    config = AssuranceConfig(
        camera_reliance_mode="recorded_camera_supporting",
        prediction_horizon_s=5.0,
        recovery_horizon_s=5.0,
    )
    assert config.camera_health_source in config.required_health_sources
    message = _retime(governor_input)
    message["health"]["perception_health_id"] = "recorded-camera-health:1"

    expiry, reasons = required_health_evidence(message, config)
    assert expiry > message["monotonic_time_ns"]
    assert reasons == ()

    neural = next(
        item for item in message["health"]["summaries"]
        if item["source_id"] == "neural_sensor_internals"
    )
    neural.update(
        status="unknown",
        capability="output_only",
        reason_codes=["RISK_BAND_NOT_HELDOUT_VALIDATED"],
    )
    _, reasons = required_health_evidence(message, config)
    assert reasons == (
        "REQUIRED_HEALTH_SOURCE_UNAVAILABLE:neural_sensor_internals",
    )
    _, recovery_reasons = required_health_evidence(message, config, recovery=True)
    assert recovery_reasons == ()


def test_unknown_recorded_camera_health_causes_real_accepted_gate_recovery(
    reference, governor_input
) -> None:
    config = AssuranceConfig(
        camera_reliance_mode="recorded_camera_supporting",
        prediction_horizon_s=5.0,
        recovery_horizon_s=5.0,
    )
    message = _retime(governor_input)
    message["health"]["perception_health_id"] = "recorded-camera-health:1"
    neural = next(
        item for item in message["health"]["summaries"]
        if item["source_id"] == "neural_sensor_internals"
    )

    healthy = copy.deepcopy(message)
    healthy_decision = A1ThresholdSimplex(reference, config).evaluate(healthy)
    assert healthy_decision["action"] == "pass"

    neural.update(
        status="unknown",
        capability="output_only",
        reason_codes=["RISK_BAND_NOT_HELDOUT_VALIDATED"],
    )
    degraded_decision = A1ThresholdSimplex(reference, config).evaluate(message)
    assert degraded_decision["action"] in {"recover", "minimum_risk"}
    assert "REQUIRED_HEALTH_SOURCE_UNAVAILABLE:neural_sensor_internals" in degraded_decision[
        "reason_codes"
    ]

    plant = RecordingPlant()
    runtime = _gate(reference, plant, config)
    try:
        receipt = runtime.submit(degraded_decision, message, token="decision-secret")
        assert receipt["accepted"], receipt
        assert len(plant.envelopes) == 1
        assert plant.envelopes[0]["authority"] == "recovery"
        assert plant.envelopes[0]["command"] == degraded_decision["issued_command"]
    finally:
        runtime.close()


def test_camera_reliance_mode_is_not_selected_by_ai_input() -> None:
    baseline = AssuranceConfig()
    assert baseline.camera_reliance_mode == "radar_only"
    assert baseline.camera_health_source not in baseline.required_health_sources
    with pytest.raises(ValueError):
        replace(baseline, camera_reliance_mode="ai_claimed_optional")
    with pytest.raises(ValueError):
        replace(
            baseline,
            camera_reliance_mode="recorded_camera_supporting",
            camera_health_source="obstacle_perception:radar",
        )


def test_unqualified_operating_mode_blocks_normal_and_independent_recovery(
    reference, governor_input
) -> None:
    config = AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    message = _retime(governor_input)
    mode = next(
        item for item in message["health"]["summaries"]
        if item["source_id"] == "operating_mode_qualification"
    )
    mode.update(
        status="unknown",
        capability="unavailable",
        reason_codes=["MARINE_MODE_NOT_ASSURANCE_QUALIFIED"],
    )
    for recovery in (False, True):
        _, reasons = required_health_evidence(message, config, recovery=recovery)
        assert reasons == (
            "REQUIRED_HEALTH_SOURCE_UNAVAILABLE:operating_mode_qualification",
        )

    decision = A1ThresholdSimplex(reference, config).evaluate(message)
    assert decision["action"] == "minimum_risk"
    assert decision["valid"] is True
    assert "REQUIRED_HEALTH_SOURCE_UNAVAILABLE:operating_mode_qualification" in decision[
        "reason_codes"
    ]
    # Canonical minimum risk remains available; no pass/recover continuation is
    # accepted under an unqualified plant model.
    plant = RecordingPlant()
    runtime = _gate(reference, plant, config)
    try:
        receipt = runtime.submit(decision, message, token="decision-secret")
        assert receipt["accepted"], receipt
        assert plant.envelopes[0]["authority"] == "recovery"
        assert plant.envelopes[0]["command"]["speed_mps"] <= 1.0
    finally:
        runtime.close()
