from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import jsonschema
import pytest

from horizon_collector.store import CollectorStore
from horizon_assurance.candidates import A1ThresholdSimplex, A5EvidenceHybrid
from horizon_assurance.configuration import AssuranceConfig, NavigationReference
from horizon_fusion import http_api as fusion_http_api
from horizon_fusion.core import FusionEngine, NotReady
from horizon_fusion.http_api import FusionLoop
from horizon_gate.core import ActuatorGate, GateConfig
from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.scenario import load_scenario
from policies import FixturePolicy
from service import DecisionAIServer


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "packages/contracts/schema/horizon.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def neural_observation(now_ns: int, *, status: str = "unknown") -> dict:
    observation_id = "camera-sequence:health:7"
    return {
        "contract_type": "Observation",
        "schema_version": "0.1.0",
        "observation_id": observation_id,
        "run_id": "perception-bridge",
        "branch_id": "protected",
        "input_group": "neural_sensor_internals",
        "source_id": "neural-health-recorded-wasrt",
        "sequence": 7,
        "time": {
            "event_time_s": 1.6,
            "received_monotonic_ns": now_ns,
            "valid_until_monotonic_ns": now_ns + 1_000_000_000,
            "clock_uncertainty_ms": 1.0,
        },
        "units": "bounded_layer_statistics",
        "frame": "model_internal",
        "capability": "output_only",
        "provenance": {
            "kind": "recorded",
            "source_id": "kope81-recorded",
            "artifact_uri": "external-recorded-camera://kope81/00006807L.jpg",
            "sha256": "1" * 64,
            "rights": "external research dataset",
        },
        "payload": {
            "frame_id": "kope81:00006807L.jpg",
            "inference_id": "kope81:wasrt:7",
            "perception_observation_id": "camera-sequence:perception:7",
            "mode": "recorded_camera_live_processing_not_pose_reactive",
            "_collector": {
                "ancestor_ids": [
                    "kope81:00006807L.jpg",
                    "camera-sequence:perception:7",
                ]
            },
            "perception_health": {
                "contract_type": "PerceptionHealth",
                "schema_version": "0.1.0",
                "health_id": "camera-sequence:health:7:H0",
                "source_id": "neural-health-recorded-wasrt",
                "method_id": "H0",
                "status": status,
                "score": None,
                "reason_codes": [
                    "MISSING_FROZEN_CALIBRATION",
                    "METRIC_GEOMETRY_UNAVAILABLE",
                    "RECORDED_CAMERA_NOT_POSE_REACTIVE",
                ],
                "calibration_version": None,
                "reference_version": None,
                "supported_scope": "recorded_camera_image_space_health_only_no_metric_contacts",
                "valid_until_monotonic_ns": now_ns + 1_000_000_000,
            },
        },
    }


def batch_with_neural(now_ns: int, observation: dict) -> dict:
    simulator = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios/crossing_recoverable.json"),
        seed=2,
        run_id="perception-bridge",
    )
    simulator.step(80)
    store = CollectorStore()
    store.update_snapshot("protected", simulator.public_snapshot())
    store.update_reference("protected", simulator.public_reference())
    for item in simulator.observation_batch():
        store.ingest(item, received_ns=now_ns, simulation_time_s=simulator.simulation_time_s)
    camera = deepcopy(observation)
    camera.update({
        "observation_id": observation["payload"]["perception_observation_id"],
        "input_group": "obstacle_perception",
        "source_id": "camera-recorded-wasrt",
        "units": "image_space_class_probabilities",
        "frame": "camera_left_rectified_pixels",
    })
    camera["payload"] = {
        "frame_id": observation["payload"]["frame_id"],
        "inference_id": observation["payload"]["inference_id"],
        "contacts": [],
        "mode": "recorded_camera_live_processing_not_pose_reactive",
    }
    store.ingest(camera, received_ns=now_ns, clock_domain="host_monotonic")
    store.ingest(observation, received_ns=now_ns, clock_domain="host_monotonic")
    return store.batch(branch="protected")


def assemble(batch: dict, now_ns: int) -> tuple[dict, FusionEngine]:
    engine = FusionEngine()
    engine.update_batch(batch, now_ns=now_ns)
    snapshot = engine.decision_snapshot(now_ns=now_ns)
    proposal, trace = FixturePolicy("nominal").propose(snapshot)
    assembly_now = max(now_ns, int(trace["completed_monotonic_ns"]))
    return engine.assemble(proposal, trace, now_ns=assembly_now), engine


def test_schema_valid_neural_health_is_bound_to_governor_and_evidence() -> None:
    now_ns = time.monotonic_ns()
    record = neural_observation(now_ns)
    governor, engine = assemble(batch_with_neural(now_ns, record), now_ns)

    VALIDATOR.validate(governor)
    health_id = record["payload"]["perception_health"]["health_id"]
    assert governor["health"]["perception_health_id"] == health_id
    assert engine.last_evidence is not None
    assert engine.last_evidence["perception_health"] == record["payload"]["perception_health"]
    assert set(engine.last_evidence["perception_health_observation_ids"]) == {
        record["observation_id"],
        record["payload"]["perception_observation_id"],
    }
    assert set(engine.last_evidence["perception_health_observation_ids"]).issubset(
        engine.last_evidence["bundle"]["observation_ids"]
    )
    assert health_id in engine.last_evidence["bundle"]["health_ids"]
    neural_summary = next(
        item for item in governor["health"]["summaries"]
        if item["source_id"] == "neural_sensor_internals"
    )
    assert neural_summary["status"] == "unknown"
    assert "MISSING_FROZEN_CALIBRATION" in neural_summary["reason_codes"]
    assert all(
        record["payload"]["perception_observation_id"]
        not in track["supporting_observation_ids"]
        for track in engine.last_evidence["tracks"]
    )


def test_original_expired_health_is_not_renewed_by_collector_receipt() -> None:
    now_ns = time.monotonic_ns()
    record = neural_observation(now_ns)
    record["time"]["received_monotonic_ns"] = now_ns - 2_000_000_000
    record["time"]["valid_until_monotonic_ns"] = now_ns - 1_000_000_000
    record["payload"]["perception_health"]["valid_until_monotonic_ns"] = now_ns - 1_000_000_000
    batch = batch_with_neural(now_ns, record)
    collected = next(item for item in batch["observations"] if item["source_id"] == record["source_id"])
    assert collected["time"]["valid_until_monotonic_ns"] <= now_ns - 1_000_000_000
    assert collected["time"]["valid_until_monotonic_ns"] < now_ns

    governor, engine = assemble(batch, now_ns)
    assert governor["health"]["perception_health_id"] is None
    assert engine.last_evidence["perception_health"] is None


def test_malformed_nested_health_cannot_become_healthy() -> None:
    now_ns = time.monotonic_ns()
    record = neural_observation(now_ns, status="healthy")
    malformed = deepcopy(record)
    malformed["payload"]["perception_health"]["valid_until_monotonic_ns"] = "later"
    governor, _ = assemble(batch_with_neural(now_ns, malformed), now_ns)

    assert governor["health"]["perception_health_id"] is None
    summary = next(
        item for item in governor["health"]["summaries"]
        if item["source_id"] == "neural_sensor_internals"
    )
    assert summary["status"] == "invalid"
    assert "PERCEPTION_HEALTH_SCHEMA_INVALID" in summary["reason_codes"]


def test_health_without_its_exact_camera_observation_is_not_bound() -> None:
    now_ns = time.monotonic_ns()
    record = neural_observation(now_ns)
    batch = batch_with_neural(now_ns, record)
    batch["observations"] = [
        item for item in batch["observations"]
        if item["observation_id"] != record["payload"]["perception_observation_id"]
    ]
    governor, _ = assemble(batch, now_ns)

    assert governor["health"]["perception_health_id"] is None
    summary = next(
        item for item in governor["health"]["summaries"]
        if item["source_id"] == "neural_sensor_internals"
    )
    assert summary["status"] == "invalid"
    assert "PERCEPTION_HEALTH_LINEAGE_INVALID" in summary["reason_codes"]


def test_external_ai_consumes_exact_ordered_snapshot_and_health_lineage() -> None:
    now_ns = time.monotonic_ns()
    record = neural_observation(now_ns)
    engine = FusionEngine()
    engine.update_batch(batch_with_neural(now_ns, record), now_ns=now_ns)
    snapshot = engine.decision_snapshot(now_ns=now_ns)
    context = engine.perception_context(now_ns=now_ns)
    assert context is not None
    assert context["source_observation_ids"] == [
        record["observation_id"],
        record["payload"]["perception_observation_id"],
    ]
    assert context["camera_free_space_usable"] is False
    assert context["metric_contacts_usable"] is False
    assert context["calibrated_risk_band"] == "unknown"

    policy = FixturePolicy("unsafe_straight", camera_reliance="recorded_camera_supporting")
    proposal, trace = policy.propose(snapshot, context)
    assert proposal["command"]["speed_mps"] == 1.0
    assert trace["consumed_input_ids"] == [snapshot["snapshot_id"], context["health_id"]]
    assembly_now = max(now_ns, int(trace["completed_monotonic_ns"]))
    governor = engine.assemble(
        proposal,
        trace,
        now_ns=assembly_now,
        request_monotonic_ns=now_ns,
        requested_perception_context=context,
    )
    VALIDATOR.validate(governor)
    assert governor["health"]["perception_health_id"] == context["health_id"]

    reversed_trace = deepcopy(trace)
    reversed_trace["consumed_input_ids"] = list(reversed(trace["consumed_input_ids"]))
    with pytest.raises(NotReady, match="AI_CONSUMPTION_LINEAGE_MISMATCH"):
        engine.assemble(
            proposal,
            reversed_trace,
            now_ns=assembly_now,
            request_monotonic_ns=now_ns,
            requested_perception_context=context,
        )


def test_decision_ai_http_boundary_rejects_camera_authority_claims() -> None:
    now_ns = time.monotonic_ns()
    record = neural_observation(now_ns)
    engine = FusionEngine()
    engine.update_batch(batch_with_neural(now_ns, record), now_ns=now_ns)
    snapshot = engine.decision_snapshot(now_ns=now_ns)
    context = engine.perception_context(now_ns=now_ns)
    assert context is not None
    server = DecisionAIServer(
        ("127.0.0.1", 0),
        FixturePolicy("nominal", camera_reliance="recorded_camera_supporting"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/propose"
        request = Request(
            url,
            data=json.dumps({"snapshot": snapshot, "perception_context": context}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=0.5) as response:
            result = json.load(response)
        assert result["inference_trace"]["consumed_input_ids"] == [
            snapshot["snapshot_id"], context["health_id"]
        ]
        assert result["proposal"]["command"]["speed_mps"] <= 1.0

        forged = deepcopy(context)
        forged["camera_free_space_usable"] = True
        forged_request = Request(
            url,
            data=json.dumps({"snapshot": snapshot, "perception_context": forged}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(forged_request, timeout=0.5)
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)


def test_live_fusion_loop_sends_context_and_requires_trace_binding(monkeypatch) -> None:
    now_ns = time.monotonic_ns()
    batch = batch_with_neural(now_ns, neural_observation(now_ns))
    loop = FusionLoop(FusionEngine(), "http://collector", "http://decision", "protected")
    captured: list[dict] = []
    policy = FixturePolicy("nominal", camera_reliance="recorded_camera_supporting")

    monkeypatch.setattr(fusion_http_api, "_get_json", lambda *_args, **_kwargs: batch)

    def propose(_url: str, request: dict) -> dict:
        captured.append(deepcopy(request))
        proposal, trace = policy.propose(
            request["snapshot"], request.get("perception_context")
        )
        return {"proposal": proposal, "inference_trace": trace}

    monkeypatch.setattr(fusion_http_api, "_post_json", propose)
    loop.cycle_once()

    assert loop.latest is not None
    assert len(captured) == 1
    context = captured[0]["perception_context"]
    assert loop.latest["health"]["perception_health_id"] == context["health_id"]
    assert context["source_observation_ids"] == [
        "camera-sequence:health:7",
        "camera-sequence:perception:7",
    ]


def test_live_camera_health_causes_assurance_correction_accepted_by_gate() -> None:
    now_ns = time.monotonic_ns()
    batch = batch_with_neural(now_ns, neural_observation(now_ns))
    engine = FusionEngine()
    engine.update_batch(batch, now_ns=now_ns)
    snapshot = engine.decision_snapshot(now_ns=now_ns)
    context = engine.perception_context(now_ns=now_ns)
    assert context is not None and context["health_status"] == "unknown"
    proposal, trace = FixturePolicy(
        "unsafe_straight", camera_reliance="recorded_camera_supporting"
    ).propose(snapshot, context)
    assembly_now = max(now_ns, int(trace["completed_monotonic_ns"]))
    governor = engine.assemble(
        proposal,
        trace,
        now_ns=assembly_now,
        request_monotonic_ns=now_ns,
        requested_perception_context=context,
    )
    config = AssuranceConfig(
        camera_reliance_mode="recorded_camera_supporting",
        prediction_horizon_s=5.0,
        recovery_horizon_s=5.0,
    )
    reference = NavigationReference.from_simulator_reference(batch["reference"])
    decision = A1ThresholdSimplex(reference, config).evaluate(governor)
    assert decision["action"] in {"recover", "minimum_risk"}
    assert "REQUIRED_HEALTH_SOURCE_UNAVAILABLE:neural_sensor_internals" in decision[
        "reason_codes"
    ]

    class Plant:
        envelopes: list[dict] = []

        def command(self, envelope: dict) -> dict:
            self.envelopes.append(deepcopy(envelope))
            received = time.monotonic_ns()
            return {
                "contract_type": "GateReceipt",
                "schema_version": "0.1.0",
                "receipt_id": "live-camera-recovery-receipt",
                "run_id": envelope["run_id"],
                "branch_id": envelope["branch_id"],
                "decision_id": envelope["decision_id"],
                "command_id": envelope["command_id"],
                "authority": envelope["authority"],
                "accepted": True,
                "reason_codes": [],
                "received_monotonic_ns": received,
                "actuated_monotonic_ns": received,
                "actual_command": envelope["command"],
            }

        def snapshot(self) -> dict:
            return {"simulation_time_s": governor["simulation_time_s"]}

    plant = Plant()
    gate = ActuatorGate(
        run_id=governor["run_id"],
        branch_id=governor["branch_id"],
        plant=plant,
        reference=reference,
        decision_token="decision-secret",
        recovery_token="recovery-secret",
        operator_token="operator-secret",
        config=GateConfig(
            startup_interlock_required=False,
            asynchronous_recovery_cache=False,
        ),
        assurance_config=config,
    )
    try:
        receipt = gate.submit(decision, governor, token="decision-secret")
        assert receipt["accepted"], receipt
        assert plant.envelopes[0]["authority"] == "recovery"
        assert plant.envelopes[0]["command"] == decision["issued_command"]
    finally:
        gate.close()


def test_ai_context_cannot_be_reused_after_original_health_expiry() -> None:
    now_ns = time.monotonic_ns()
    record = neural_observation(now_ns)
    record["time"]["valid_until_monotonic_ns"] = now_ns + 10_000_000
    record["payload"]["perception_health"]["valid_until_monotonic_ns"] = now_ns + 10_000_000
    engine = FusionEngine()
    engine.update_batch(batch_with_neural(now_ns, record), now_ns=now_ns)
    snapshot = engine.decision_snapshot(now_ns=now_ns)
    context = engine.perception_context(now_ns=now_ns)
    assert context is not None
    proposal, trace = FixturePolicy("nominal").propose(snapshot, context)

    after_expiry = int(context["valid_until_monotonic_ns"]) + 1
    with pytest.raises(NotReady) as error:
        engine.assemble(
            proposal,
            trace,
            now_ns=after_expiry,
            request_monotonic_ns=now_ns,
            requested_perception_context=context,
        )
    assert "AI_PERCEPTION_CONTEXT_STALE_OR_REPLACED" in error.value.reasons


def test_unqualified_marine_mode_is_a_required_unavailable_health_leaf() -> None:
    now_ns = time.monotonic_ns()
    record = neural_observation(now_ns)
    batch = batch_with_neural(now_ns, record)
    batch["reference"]["model_version"] = "synthetic-12m-coupled-marine-v1"
    batch["reference"]["operating_mode_qualification"] = {
        "plant_mode_id": "synthetic-12m-coupled-marine-v1",
        "physical_model_status": "characterized",
        "assurance_status": "unknown",
        "reason_codes": ["MARINE_MODE_NOT_ASSURANCE_QUALIFIED"],
        "config_sha256": "4" * 64,
    }

    governor, _ = assemble(batch, now_ns)
    mode = next(
        item for item in governor["health"]["summaries"]
        if item["source_id"] == "operating_mode_qualification"
    )
    assert mode["status"] == "unknown"
    assert mode["capability"] == "unavailable"
    assert "MARINE_MODE_NOT_ASSURANCE_QUALIFIED" in mode["reason_codes"]
    assert mode["valid_until_monotonic_ns"] > now_ns


def test_exact_legacy_baseline_is_the_only_missing_field_compatibility() -> None:
    now_ns = time.monotonic_ns()
    record = neural_observation(now_ns)
    baseline_batch = batch_with_neural(now_ns, record)
    baseline_batch["reference"].pop("operating_mode_qualification")
    baseline, _ = assemble(baseline_batch, now_ns)
    baseline_mode = next(
        item for item in baseline["health"]["summaries"]
        if item["source_id"] == "operating_mode_qualification"
    )
    assert baseline_mode["status"] == "healthy"
    assert "BASELINE_ASSURANCE_CONFIGURATION_COMPATIBILITY" in baseline_mode["reason_codes"]

    batch = batch_with_neural(now_ns, record)
    batch["reference"]["model_version"] = "future-unqualified-model"
    batch["reference"].pop("operating_mode_qualification")
    future, _ = assemble(batch, now_ns)
    future_mode = next(
        item for item in future["health"]["summaries"]
        if item["source_id"] == "operating_mode_qualification"
    )
    assert future_mode["status"] == "unknown"
    assert future_mode["capability"] == "unavailable"
    assert future_mode["reason_codes"] == ["OPERATING_MODE_QUALIFICATION_MISSING"]


@pytest.mark.parametrize("score, duplicates, expected_speed", [
    (1.0, None, 4.0), (3.0, None, 1.0), (None, None, 4.0),
    (1.0, 2, 4.0), (1.0, 3, 1.0), (None, 3, 1.0),
])
def test_h5_simulation_warning_changes_proposal_and_reaches_a5_gate(score, duplicates, expected_speed) -> None:
    now_ns = time.monotonic_ns()
    record = neural_observation(now_ns)
    record["payload"]["perception_health"].update(method_id="H5", score=score)
    warning = {
        "mode": "simulation_warning",
        "status": "unknown" if score is None else ("warning" if score >= 2.7 else "below_threshold"),
        "score": score,
        "threshold": 2.7,
        "reference_hash": "a" * 64,
    }
    if duplicates is not None:
        warning["frozen_feed"] = {
            "method": "exact_decoded_rgb_repeat", "minimum_consecutive_duplicates": 3,
            "consecutive_duplicates": duplicates,
            "status": "warning" if duplicates >= 3 else "below_threshold",
        }
        if duplicates >= 3:
            warning["status"] = "warning"
    record["payload"]["simulation_h5_warning"] = warning
    batch = batch_with_neural(now_ns, record)
    engine = FusionEngine()
    engine.update_batch(batch, now_ns=now_ns)
    snapshot = engine.decision_snapshot(now_ns=now_ns)
    context = engine.perception_context(now_ns=now_ns)
    assert context["simulation_h5_warning"] == warning
    assert context["health_status"] == "unknown"
    assert context["camera_free_space_usable"] is False
    policy = FixturePolicy("nominal")
    proposal, trace = policy.propose(snapshot, context)
    assert proposal["command"]["speed_mps"] == expected_speed
    assert trace["candidate_scores"]["h5_simulation_warning"] == (1.0 if expected_speed == 1.0 else 0.0)
    assert context["health_id"] in trace["consumed_input_ids"]
    governor = engine.assemble(
        proposal, trace,
        now_ns=max(now_ns, trace["completed_monotonic_ns"]),
        request_monotonic_ns=now_ns, requested_perception_context=context,
    )
    reference = NavigationReference.from_simulator_reference(batch["reference"])
    config = AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    decision = A5EvidenceHybrid(reference, config).evaluate(governor)
    assert decision["action"] == "pass", decision
    assert decision["issued_command"]["speed_mps"] == expected_speed

    class Plant:
        def __init__(self):
            self.envelopes = []

        def command(self, envelope):
            self.envelopes.append(deepcopy(envelope))
            received = time.monotonic_ns()
            return {
                "contract_type": "GateReceipt", "schema_version": "0.1.0",
                "receipt_id": "h5-demo-receipt", "run_id": envelope["run_id"],
                "branch_id": envelope["branch_id"], "decision_id": envelope["decision_id"],
                "command_id": envelope["command_id"], "authority": envelope["authority"],
                "accepted": True, "reason_codes": [],
                "received_monotonic_ns": received, "actuated_monotonic_ns": received,
                "actual_command": envelope["command"],
            }

        def snapshot(self):
            return {"simulation_time_s": governor["simulation_time_s"]}

    plant = Plant()
    gate = ActuatorGate(
        run_id=governor["run_id"], branch_id=governor["branch_id"],
        plant=plant, reference=reference, decision_token="decision-secret",
        recovery_token="recovery-secret", operator_token="operator-secret",
        config=GateConfig(startup_interlock_required=False, asynchronous_recovery_cache=False),
        assurance_config=config,
    )
    try:
        receipt = gate.submit(decision, governor, token="decision-secret")
        assert receipt["accepted"], receipt
        assert plant.envelopes[0]["command"]["speed_mps"] == expected_speed
    finally:
        gate.close()

    expired_context = deepcopy(context)
    expired_context["valid_until_monotonic_ns"] = now_ns - 1
    expired_proposal, _ = FixturePolicy("nominal").propose(snapshot, expired_context)
    assert expired_proposal["command"]["speed_mps"] == 4.0
    assert engine.perception_context(now_ns=now_ns + 2_000_000_000) is None
    real_snapshot = deepcopy(snapshot)
    real_snapshot["display_only"] = False
    assert not FixturePolicy._simulation_h5_warning(real_snapshot, context, now_ns)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -1, True])
def test_invalid_h5_demo_scores_cannot_drive_fixture(score) -> None:
    now_ns = time.monotonic_ns()
    context = {
        "method_id": "H5", "valid_until_monotonic_ns": now_ns + 1_000_000,
        "simulation_h5_warning": {
            "mode": "simulation_warning", "status": "warning", "score": score, "threshold": 2.7,
        },
    }
    with pytest.raises(ValueError, match="invalid H5 simulation score"):
        FixturePolicy._simulation_h5_warning(
            {"contract_type": "SimulationSnapshot", "display_only": True}, context, now_ns
        )


@pytest.mark.parametrize("overrides", [
    {"consecutive_duplicates": True}, {"consecutive_duplicates": -1},
    {"minimum_consecutive_duplicates": 0}, {"status": "below_threshold"},
    {"status": "unknown"}, {"method": "unsupported"},
])
def test_invalid_h5_freeze_evidence_cannot_drive_fixture(overrides):
    now_ns = time.monotonic_ns()
    context = {
        "method_id": "H5", "valid_until_monotonic_ns": now_ns + 1_000_000,
        "simulation_h5_warning": {
            "mode": "simulation_warning", "status": "warning", "score": 0.1, "threshold": 2.7,
            "frozen_feed": {
                "method": "exact_decoded_rgb_repeat", "minimum_consecutive_duplicates": 3,
                "consecutive_duplicates": 3, "status": "warning", **overrides,
            },
        },
    }
    with pytest.raises(ValueError, match="frozen feed"):
        FixturePolicy._simulation_h5_warning(
            {"contract_type": "SimulationSnapshot", "display_only": True}, context, now_ns
        )
