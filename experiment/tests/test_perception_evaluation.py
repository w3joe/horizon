from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from experiment.evaluation.perception import (
    calibrate_perception_methods,
    compare_runtime_drift,
    evaluate_h4_claim_gate,
)
from experiment.evaluation.controller_evidence import assess_controller_evidence


def _hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _summary(values):
    return {
        "finite": True,
        "pooled_mean": values,
        "pooled_standard_deviation": [value / 2 for value in values],
    }


def _drift_fixture(tmp_path: Path):
    preprocessing = "1" * 64
    split_hash = "2" * 64
    inputs = {"0001L.jpg": "3" * 64, "0002L.jpg": "4" * 64}
    base_manifest = {
        "evidence_partition": "development",
        "sequence": "/data/kope81-00-00006800-00007095/frames",
        "sequence_frame_count": 2,
        "source_commit": "source-pin",
        "weights_sha256": "5" * 64,
        "device": "mps",
        "fp16": False,
        "input_sha256": inputs,
        "environment": {"preprocessing": {"sha256": preprocessing}},
        "instrumentation_validation": {
            "outputs_identical": True,
            "reset_reproducible": True,
        },
    }
    candidate_manifest = {
        **base_manifest,
        "device": "cuda:0",
        "fp16": True,
        "split_manifest_sha256": split_hash,
    }
    base_manifest_path = tmp_path / "base-manifest.json"
    candidate_manifest_path = tmp_path / "candidate-manifest.json"
    _write_json(base_manifest_path, base_manifest)
    _write_json(candidate_manifest_path, candidate_manifest)
    config = {
        "dataset": "MODD2_RAW",
        "analysis_seed": 7,
        "split_manifest_sha256": split_hash,
        "development_sequence_id": "kope81-00-00006800-00007095",
        "development_frame_count": 2,
        "model": {
            "source_commit": "source-pin",
            "weights_sha256": "5" * 64,
            "preprocessing_sha256": preprocessing,
        },
        "drift": {
            "reference": {
                "runtime_id": "mps-fp32",
                "device_kind": "mps",
                "fp16": False,
                "manifest_sha256": hashlib.sha256(base_manifest_path.read_bytes()).hexdigest(),
            },
            "candidate": {
                "runtime_id": "cuda-fp16",
                "device_kind": "cuda",
                "fp16": True,
                "split_manifest_sha256": split_hash,
            },
            "layers": {
                "encoder": {"statistics": ["pooled_mean", "pooled_standard_deviation"]},
            },
            "output_health_fields": ["entropy_p95"],
        },
    }
    config_path = tmp_path / "config.json"
    _write_json(config_path, config)
    rows = [
        {
            "frame_id": frame,
            "activation_summaries": {"encoder": _summary([1.0 + index, 2.0])},
            "output_health": {"entropy_p95": 0.1 + index / 10},
        }
        for index, frame in enumerate(inputs)
    ]
    candidate_rows = json.loads(json.dumps(rows))
    candidate_rows[1]["activation_summaries"]["encoder"]["pooled_mean"][0] += 0.01
    candidate_rows[1]["output_health"]["entropy_p95"] += 0.02
    base_features = tmp_path / "base.jsonl"
    candidate_features = tmp_path / "candidate.jsonl"
    _write_jsonl(base_features, rows)
    _write_jsonl(candidate_features, candidate_rows)
    return config_path, base_manifest_path, base_features, candidate_manifest_path, candidate_features


def test_runtime_drift_is_paired_and_does_not_invent_equivalence(tmp_path: Path) -> None:
    inputs = _drift_fixture(tmp_path)
    report = compare_runtime_drift(*inputs)

    assert report["frame_count"] == 2
    assert report["claim_gate"] == {
        "status": "descriptive_development_only",
        "cross_device_equivalence_claim": False,
        "acceptance_threshold_fitted": False,
        "reason": "one paired sequence cannot estimate a drift acceptance distribution",
        "nonzero_representation_drift": True,
        "target_runtime_reference_required": True,
    }
    assert report["activation_drift"]["encoder"]["maximum_absolute_difference"]["maximum"] == pytest.approx(0.01)
    assert report["heldout_observations_used"] == 0


def test_runtime_drift_rejects_unpaired_inputs(tmp_path: Path) -> None:
    inputs = list(_drift_fixture(tmp_path))
    candidate = json.loads(inputs[3].read_text())
    candidate["input_sha256"]["0002L.jpg"] = "9" * 64
    _write_json(inputs[3], candidate)

    with pytest.raises(ValueError, match="paired input hashes differ"):
        compare_runtime_drift(*inputs)


def _calibration_fixture(tmp_path: Path):
    config = {
        "dataset": "MODD2_RAW",
        "analysis_seed": 17,
        "split_manifest_sha256": "a" * 64,
        "target_runtime_id": "cuda-fp16-v1",
        "calibration_collection_groups": ["kope67"],
        "calibration_sequences": ["kope67-sequence"],
        "calibration_frame_count": 4,
        "calibration_sequence_frame_counts": {"kope67-sequence": 4},
        "max_false_alarm_rate": 0.25,
        "label_policy": {
            "id": "bbox-zero-v1",
            "miss_definition": "any zero count",
            "official_metric": False,
        },
        "controlled_perturbations": [
            {"id": "blur-v1", "seed": 41, "parameters": {"sigma": 1.5}}
        ],
        "h4_claim_gate": {
            "minimum_pairs": 12,
            "minimum_sequences": 3,
            "minimum_seeds": 3,
            "minimum_win_fraction": 0.75,
            "maximum_sign_test_p": 0.05,
        },
    }
    config_path = tmp_path / "config.json"
    _write_json(config_path, config)
    label_policy_hash = _hash(config["label_policy"])
    gates = {
        "H0": {"score_ready": True},
        "H1": {"score_ready": False, "reason": "independent_horizon_unavailable"},
        "H2": {
            "score_ready": True,
            "reference_fit_split": "development",
            "reference_hash": "h2-reference",
            "reference_runtime_id": "cuda-fp16-v1",
        },
        "H3": {
            "score_ready": True,
            "reference_fit_split": "development",
            "reference_hash": "h3-reference",
            "reference_runtime_id": "cuda-fp16-v1",
        },
        "H4": {
            "score_ready": True,
            "reference_fit_split": "development",
            "reference_hash": "h4-reference",
            "reference_runtime_id": "cuda-fp16-v1",
            "reference_parameters": {"converged": False},
            "causal_controls": [],
        },
    }
    rows = []
    perturbation_hash = _hash(config["controlled_perturbations"][0]["parameters"])
    for method_index, method_id in enumerate(("H0", "H2", "H3")):
        reference_hash = gates[method_id].get("reference_hash")
        for index in range(4):
            base_id = f"base-{index}"
            common = {
                "method_id": method_id,
                "base_sample_id": base_id,
                "sequence_id": "kope67-sequence",
                "collection_group": "kope67",
                "frame_id": f"{index:04d}L.jpg",
                "source_frame_sha256": f"{index + 1:x}" * 64,
                "reference_hash": reference_hash,
            }
            for controlled in (False, True):
                count = 0 if controlled and index == 3 else 8
                row = {
                    **common,
                    "sample_id": f"{method_id}-{base_id}-{'blur' if controlled else 'nominal'}",
                    "score": (0.8 if controlled else 0.1) + method_index / 100 + index / 1000,
                    "condition": (
                        {
                            "kind": "controlled",
                            "perturbation_id": "blur-v1",
                            "seed": 41,
                            "parameters_hash": perturbation_hash,
                        }
                        if controlled
                        else {"kind": "nominal", "perturbation_id": None, "seed": None}
                    ),
                    "label": {
                        "policy_hash": label_policy_hash,
                        "annotation_sha256": f"{index + 5:x}" * 64,
                        "annotated_obstacle_count": 1,
                        "per_object_obstacle_pixel_count": [count],
                        "missed_obstacle": count == 0,
                        "evaluable": True,
                    },
                }
                rows.append(row)
    bundle = {
        "partition": "calibration",
        "heldout_observations_used": 0,
        "split_manifest_sha256": config["split_manifest_sha256"],
        "analysis_seed": config["analysis_seed"],
        "dataset": config["dataset"],
        "label_policy_hash": label_policy_hash,
        "method_gates": gates,
        "extraction_manifests": [
            {
                "sequence_id": "kope67-sequence",
                "runtime_id": "cuda-fp16-v1",
                "frame_count": 4,
                "manifest_sha256": "d" * 64,
                "features_sha256": "e" * 64,
            }
        ],
        "samples": rows,
    }
    bundle_path = tmp_path / "bundle.json"
    _write_json(bundle_path, bundle)
    return config_path, bundle_path


def test_perception_calibration_matches_arms_and_blocks_unready_methods(tmp_path: Path) -> None:
    report = calibrate_perception_methods(*_calibration_fixture(tmp_path))

    assert report["paired_arm_count"] == 8
    assert report["method_results"]["H0"]["false_alarm_rate"] <= 0.25
    assert report["method_results"]["H2"]["controlled_fault_detection_rate"] == 1.0
    assert report["method_results"]["H1"]["status"] == "blocked"
    assert report["method_results"]["H4"]["claim_gate"]["reason"] == "reference_not_converged"
    assert report["heldout_observations_used"] == 0
    assert report["heldout_risk_validated"] is False


def test_perception_calibration_rejects_inconsistent_proxy_label(tmp_path: Path) -> None:
    config_path, bundle_path = _calibration_fixture(tmp_path)
    bundle = json.loads(bundle_path.read_text())
    bundle["samples"][0]["label"]["missed_obstacle"] = True
    _write_json(bundle_path, bundle)

    with pytest.raises(ValueError, match="missed-obstacle label is inconsistent"):
        calibrate_perception_methods(config_path, bundle_path)


def test_h4_claim_gate_requires_convergence_and_replicated_controls() -> None:
    requirements = {
        "minimum_pairs": 12,
        "minimum_sequences": 3,
        "minimum_seeds": 3,
        "minimum_win_fraction": 0.75,
        "maximum_sign_test_p": 0.05,
    }
    controls = [
        {
            "pair_id": f"pair-{index}",
            "sequence_id": f"sequence-{index % 3}",
            "seed": index % 3,
            "state_reset_verified": True,
            "context_hash": f"context-{index}",
            "target_delta": 2.0,
            "random_control_delta": 1.0,
            "equal_norm_control_delta": 0.5,
        }
        for index in range(12)
    ]
    passed = evaluate_h4_claim_gate(
        {"reference_parameters": {"converged": True}, "causal_controls": controls},
        requirements,
    )
    blocked = evaluate_h4_claim_gate(
        {"reference_parameters": {"converged": False}, "causal_controls": controls},
        requirements,
    )

    assert passed["eligible"] is True
    assert passed["one_sided_sign_test_p"] == pytest.approx(1 / 4096)
    assert blocked == {"eligible": False, "reason": "reference_not_converged"}


def _controller_record(candidate: str, runtime, boundary=2.0, recoverability="declared_recoverable"):
    return {
        "candidate_id": candidate,
        "episode_id": "episode-1",
        "branch_id": f"{candidate}-branch",
        "split": "development",
        "recoverability_class": recoverability,
        "violations": {"collision_count": 0, "grounding_count": 0, "boundary_count": 0},
        "intervention": {"last_recovery_opportunity_s": boundary},
        "runtime_ns": runtime,
        "deadline_misses": 0,
        "gate": {"assessment_status": "complete", "unsafe_or_stale_accepted_count": 0},
        "trace_complete": True,
        "mission": {"censored": True},
    }


def test_controller_evidence_keeps_odd_failures_and_zero_opportunity_unknown(tmp_path: Path) -> None:
    selection = {
        "safety_gate": {
            "preventable_violations_allowed": 0,
            "unsafe_or_stale_gate_acceptances_allowed": 0,
        },
        "runtime_gate": {"deadline_misses_allowed_in_declared_acceptance_load": 0},
    }
    index = {
        "fixture_only": False,
        "records": [_controller_record("A1", []), _controller_record("A3", [10_000])],
        "assumption_audits": [
            {
                "branch_id": "A1-branch",
                "candidate_id": "A1",
                "configured_assumptions": {
                    "contact": {"assumption_id": "radar-contact-bound-v1"}
                },
                "violated_assumption_ids": ["radar-contact-bound-v1"],
            },
            {
                "branch_id": "A3-branch",
                "candidate_id": "A3",
                "configured_assumptions": {
                    "contact": {"assumption_id": "radar-contact-bound-v1"}
                },
                "violated_assumption_ids": [],
            },
        ],
    }
    selection_path = tmp_path / "selection.json"
    index_path = tmp_path / "index.json"
    _write_json(selection_path, selection)
    _write_json(index_path, index)

    report = assess_controller_evidence(index_path, selection_path)

    assert report["candidate_results"]["A1"]["out_of_domain_episode_count"] == 1
    assert report["candidate_results"]["A1"]["gates"]["candidate_compute_deadline"] == "unknown"
    assert report["candidate_results"]["A3"]["gates"]["candidate_compute_deadline"] == "pass"
    assert report["recommendation"] is None
    assert report["recommendation_status"] == "withheld_incomplete_or_failed_evidence"
