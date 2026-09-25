from __future__ import annotations

import copy
from dataclasses import replace
from datetime import timedelta
import json
import math

import pytest

from horizon_assurance.candidates import A5EvidenceHybrid
from horizon_assurance.configuration import AssuranceConfig
from horizon_assurance.policy_enforcement import A6PolicyEnforcer, FilePolicyEvidence, content_hash
from horizon_assurance.validation import _contract_validator
from horizon_gate.core import ActuatorGate, GateConfig
from horizon_sim.clock import ManualMonotonicClock

from test_a6_policy_shadow import NOW, _bundle, _context
from test_gate import FakePlant, retime_live


def evidence_record(message, decision):
    return {
        "contract_type": "PolicyEvidence", "schema_version": "0.1.0",
        "run_id": message["run_id"], "branch_id": message["branch_id"],
        "tick_index": message["tick_index"],
        "snapshot_sha256": content_hash(message["snapshot"]),
        "command_sha256": content_hash(decision["issued_command"]),
        "observed_monotonic_ns": message["monotonic_time_ns"],
        "expires_monotonic_ns": decision["expires_monotonic_ns"],
        "provenance": "synthetic",
        "operational_context": {**_context(), "in_narrow_channel": False,
                                "in_traffic_separation_scheme": False},
        "evidence": {
            "lookout": {key: True for key in (
                "visual_watch_available", "auditory_watch_available", "radar_watch_available",
                "remote_supervisor_available", "evidence_fresh")},
            "safe_speed": {"stopping_distance_m": 10.0, "clear_distance_m": 100.0,
                           "traffic_assessment_available": True},
            "encounters": [],
        },
    }


def setup(reference, message, provider=evidence_record, **kwargs):
    message["snapshot"]["contacts"] = []
    config = AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    decision = A5EvidenceHybrid(reference, config).evaluate(message)
    assert decision["action"] == "pass", decision
    clock = ManualMonotonicClock(message["monotonic_time_ns"])
    enforcer = A6PolicyEnforcer(_bundle(), provider, allow_synthetic=True,
                                utc_now=lambda: NOW, **kwargs)
    gate = ActuatorGate(run_id=message["run_id"], branch_id=message["branch_id"],
        plant=FakePlant(), reference=reference, assurance_config=config,
        config=GateConfig(a6_mode="enforce", startup_interlock_required=False,
                          asynchronous_recovery_cache=False),
        monotonic_ns=clock, policy_enforcer=enforcer)
    return gate, decision, clock


def test_authorized_command_reaches_plant_and_binds_complete_input(reference, governor_input):
    gate, decision, _ = setup(reference, governor_input)
    receipt = gate.submit(decision, governor_input, token=gate.decision_token)
    assert receipt["accepted"]
    assert receipt["authority"] == "autonomy"
    assert receipt["actual_command"] == decision["issued_command"]
    record = gate.last_policy_decision
    _contract_validator("A6PolicyDecision").validate(record)
    assert record["authorization"] == "authorize"
    assert record["decision_sha256"] == content_hash(decision)
    assert record["input_sha256"] == content_hash(governor_input)


@pytest.mark.parametrize("fault", ["missing", "stale", "future", "command", "snapshot", "run", "tick",
    "lookout", "speed", "restricted", "channel", "synthetic", "malformed", "exception", "late", "bundle"])
def test_policy_failures_never_actuate_normal_autonomy(reference, governor_input, fault):
    def provider(message, decision):
        record = evidence_record(message, decision)
        if fault == "missing": return None
        if fault == "exception": raise OSError("adapter unavailable")
        if fault == "stale": record["expires_monotonic_ns"] = message["monotonic_time_ns"]
        if fault == "future": record["observed_monotonic_ns"] += 1_000_000_000
        if fault == "command": record["command_sha256"] = "0" * 64
        if fault == "snapshot": record["snapshot_sha256"] = "0" * 64
        if fault == "run": record["run_id"] = "another-run"
        if fault == "tick": record["tick_index"] += 1
        if fault == "lookout": record["evidence"]["lookout"]["auditory_watch_available"] = False
        if fault == "speed": record["evidence"]["safe_speed"]["clear_distance_m"] = 1.0
        if fault == "restricted": record["operational_context"]["visibility"] = "restricted"
        if fault == "channel": record["operational_context"]["in_narrow_channel"] = True
        if fault == "malformed": record["expires_monotonic_ns"] = True
        return record
    gate, decision, _ = setup(reference, governor_input, provider,
                             maximum_compute_ns=0 if fault == "late" else 100_000_000)
    if fault == "synthetic": gate.policy_enforcer.allow_synthetic = False
    if fault == "bundle": gate.policy_enforcer.bundle = _bundle(valid_until=NOW)
    receipt = gate.submit(decision, governor_input, token=gate.decision_token)
    assert gate.last_policy_decision["authorization"] == "withhold"
    assert receipt["accepted"]
    assert receipt["authority"] == "recovery"
    assert receipt["actual_command"]["speed_mps"] <= 1.0
    assert "A6_EMERGENCY_POLICY_OVERRIDE" in receipt["reason_codes"]
    assert all(item["authority"] == "recovery" for item in gate.plant.envelopes)


def test_policy_denial_uses_fresh_independently_checked_recovery(reference, governor_input):
    governor_input = retime_live(governor_input)
    gate, decision, clock = setup(reference, governor_input, lambda *_: None)
    assert gate.prime_recovery(governor_input, token=gate.decision_token)[0]
    command = copy.deepcopy(gate.stored_recovery.command)
    expiry = gate.stored_recovery.host_valid_until_ns
    receipt = gate.submit(decision, governor_input, token=gate.decision_token)
    assert receipt["accepted"] and receipt["authority"] == "recovery"
    assert receipt["actual_command"] == command
    assert gate.stored_recovery.host_valid_until_ns <= expiry
    assert gate.recovery_latched


def test_expiry_during_policy_evaluation_prevents_dispatch(reference, governor_input):
    gate, decision, clock = setup(reference, governor_input)
    def provider(message, selected):
        record = evidence_record(message, selected)
        record["expires_monotonic_ns"] = clock() + 1
        clock.advance_ns(2)
        return record
    gate.policy_enforcer.evidence_provider = provider
    receipt = gate.submit(decision, governor_input, token=gate.decision_token)
    assert not receipt["accepted"]
    assert not gate.plant.envelopes


def test_action_claims_cannot_hide_a_port_turn(reference, governor_input):
    gate, decision, _ = setup(reference, governor_input)
    governor_input["snapshot"]["contacts"] = [{"contact_id": "target"}]
    decision["issued_command"]["heading_rad"] = governor_input["snapshot"]["ownship"]["heading_rad"] - math.radians(30)
    def provider(message, selected):
        record = evidence_record(message, selected)
        record["evidence"]["encounters"] = [{
            "contact_id": "target", "in_sight": True, "power_driven": True,
            "relative_bearing_deg": 0.0, "ownship_bearing_from_contact_deg": 0.0,
            "course_difference_deg": 180.0, "risk_doubt": True,
            "treated_as_collision_risk": True, "action_lead_time_s": 60.0,
            "course_change_deg": 30.0, "speed_reduction_mps": 2.0,
            "action_detectable": True,
        }]
        return record
    gate.policy_enforcer.evidence_provider = provider
    report = gate.policy_enforcer.evaluate(governor_input, decision, now_ns=governor_input["monotonic_time_ns"])
    assert report["authorization"] == "withhold"
    assert any(item["rule_id"] == "R16" and item["finding"] == "not_satisfied" for item in report["findings"])


def test_reset_during_policy_evaluation_invalidates_reservation(reference, governor_input):
    gate, decision, _ = setup(reference, governor_input)
    def provider(message, selected):
        gate.reset_handshake(token=gate.operator_token, plant_epoch=1)
        return evidence_record(message, selected)
    gate.policy_enforcer.evidence_provider = provider
    receipt = gate.submit(decision, governor_input, token=gate.decision_token)
    assert not receipt["accepted"]
    assert "STALE_VALIDATION_COMPLETION" in receipt["reason_codes"]
    assert not gate.plant.envelopes


def test_untrusted_provider_cannot_mutate_selected_command(reference, governor_input):
    gate, decision, _ = setup(reference, governor_input)
    original = copy.deepcopy(decision)
    def provider(message, selected):
        result = evidence_record(message, selected)
        selected["issued_command"]["speed_mps"] = 50.0
        message["snapshot"]["ownship"]["heading_rad"] = 2.0
        return result
    gate.policy_enforcer.evidence_provider = provider
    receipt = gate.submit(decision, governor_input, token=gate.decision_token)
    assert receipt["accepted"]
    assert receipt["actual_command"] == original["issued_command"]
    assert decision == original


def test_replay_after_authorization_cannot_dispatch_twice(reference, governor_input):
    gate, decision, _ = setup(reference, governor_input)
    assert gate.submit(decision, governor_input, token=gate.decision_token)["accepted"]
    receipt = gate.submit(decision, governor_input, token=gate.decision_token)
    assert not receipt["accepted"]
    assert len(gate.plant.envelopes) == 1


def test_missing_policy_does_not_block_watchdog(reference, governor_input):
    governor_input = retime_live(governor_input)
    gate, decision, clock = setup(reference, governor_input, lambda *_: None)
    assert gate.prime_recovery(governor_input, token=gate.decision_token)[0]
    clock.advance_ns(160_000_000)
    receipt = gate.watchdog_tick()
    assert receipt["accepted"]
    assert receipt["authority"] == "gate_watchdog"
    assert "A6_EMERGENCY_POLICY_OVERRIDE" in gate.telemetry[-1]["reason_codes"]


def test_source_artifact_hash_is_checked_at_load(tmp_path):
    from dataclasses import asdict
    bundle = _bundle()
    config = {"provenance": "recorded", "bundle": asdict(bundle),
              "source_artifacts": {pin.source_id: "wrong.txt" for pin in bundle.metadata.source_pins}}
    (tmp_path / "config.json").write_text(json.dumps(config))
    (tmp_path / "wrong.txt").write_text("wrong source content")
    with pytest.raises(ValueError, match="source hash mismatch"):
        A6PolicyEnforcer.from_files(tmp_path / "config.json", tmp_path / "evidence.json")
    config["provenance"] = "synthetic"
    (tmp_path / "config.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="must be recorded"):
        A6PolicyEnforcer.from_files(tmp_path / "config.json", tmp_path / "evidence.json")


def test_live_file_adapter_binds_braking_to_actual_speed(tmp_path, reference, governor_input):
    gate, decision, _ = setup(reference, governor_input)
    record = evidence_record(governor_input, decision)
    record["command_sha256"] = None
    record["evidence"]["safe_speed"].update(minimum_deceleration_mps2=0.5, maximum_response_time_s=1.0)
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(record))
    adapted = FilePolicyEvidence(path)(governor_input, decision)
    own_speed = math.hypot(*governor_input["snapshot"]["ownship"]["velocity_body_mps"][:2])
    braking_speed = max(own_speed, decision["issued_command"]["speed_mps"])
    assert adapted["evidence"]["safe_speed"]["stopping_distance_m"] == braking_speed + braking_speed**2
    assert adapted["command_sha256"] == content_hash(decision["issued_command"])


def test_simulated_clock_recovery_prime_maps_host_budget(reference, governor_input):
    gate, _, _ = setup(reference, governor_input)
    accepted, reasons = gate.prime_recovery(governor_input, token=gate.decision_token)
    assert accepted, reasons
    assert gate.stored_recovery is not None
