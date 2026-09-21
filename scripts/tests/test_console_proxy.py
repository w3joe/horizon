from __future__ import annotations

import json
import hashlib
from http import HTTPStatus
from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from console_proxy import (  # noqa: E402
    ConsoleHandler,
    compact_frame,
    load_artifact_frames,
    resolve_public_route,
    validate_artifact,
)


def test_public_proxy_routes_are_explicitly_allowlisted() -> None:
    assert resolve_public_route("/api/v1/public/snapshot?branch=protected") == (
        "simulator",
        "/v1/public/snapshot?branch=protected",
    )
    assert resolve_public_route("/api/fusion/v1/governor-input?branch=protected") == (
        "fusion",
        "/v1/governor-input?branch=protected",
    )
    assert resolve_public_route("/api/assurance/v1/evidence/latest") == (
        "assurance",
        "/v1/evidence/latest",
    )
    assert resolve_public_route("/api/v1/evaluation/truth") is None
    assert resolve_public_route("/api/gate/v1/decision") is None


def test_compact_artifact_frame_omits_pooled_vectors(tmp_path: Path) -> None:
    record = {
        "frame_id": "00000.jpg",
        "sequence_id": "sequence",
        "timestamp_ns": 0,
        "timestamp_source": "synthetic_10hz_order_only",
        "buffer_age": 0,
        "cold_start": True,
        "inference_ms": 10.0,
        "instrumentation_ms": 1.0,
        "source_image_size": [512, 384],
        "model_input_size": [512, 384],
        "activation_summaries": {
            "encoder": {
                "name": "encoder",
                "shape": [1, 2048, 48, 64],
                "dtype": "torch.float32",
                "finite": True,
                "minimum": 0.0,
                "maximum": 5.0,
                "mean": 0.1,
                "standard_deviation": 0.2,
                "pooled_mean": [0.1] * 2048,
                "pooled_standard_deviation": [0.2] * 2048,
            }
        },
    }
    (tmp_path / "features.jsonl").write_text(json.dumps(record) + "\n")
    frame = load_artifact_frames(tmp_path)["00000"]
    assert frame == compact_frame(record)
    encoded = json.dumps(frame)
    assert "pooled_mean" not in encoded
    assert frame["layers"]["encoder"]["std"] == 0.2
    assert frame["artifacts"]["raw_image_url"].endswith("/00000.jpg")


def test_incomplete_artifact_is_unavailable_without_startup_error(tmp_path: Path) -> None:
    manifest, frames, error = validate_artifact(tmp_path / "missing", tmp_path / "source")
    assert manifest is None
    assert frames == {}
    assert error == "FileNotFoundError"


def test_artifact_requires_all_85_hashed_inputs_and_outputs(tmp_path: Path) -> None:
    output = tmp_path / "output"
    source = tmp_path / "source"
    (output / "class_masks").mkdir(parents=True)
    (output / "mask_previews").mkdir()
    source.mkdir()
    hashes = {}
    with (output / "features.jsonl").open("w") as features:
        for index in range(85):
            stem = f"{index:05d}"
            raw = f"raw-{index}".encode()
            (source / f"{stem}.jpg").write_bytes(raw)
            hashes[f"{stem}.jpg"] = hashlib.sha256(raw).hexdigest()
            (output / "class_masks" / f"{stem}.png").write_bytes(b"mask")
            (output / "mask_previews" / f"{stem}.png").write_bytes(b"preview")
            record = {
                "frame_id": f"{stem}.jpg",
                "sequence_id": "sequence",
                "timestamp_ns": index * 100_000_000,
                "timestamp_source": "synthetic_10hz_order_only",
                "buffer_age": 0,
                "cold_start": index == 0,
                "inference_ms": 10.0,
                "instrumentation_ms": 1.0,
                "source_image_size": [512, 384],
                "model_input_size": [512, 384],
                "activation_summaries": {
                    "encoder": {
                        "name": "encoder",
                        "shape": [1, 1, 1, 1],
                        "dtype": "torch.float32",
                        "finite": True,
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "mean": 0.5,
                        "standard_deviation": 0.1,
                    }
                },
            }
            features.write(json.dumps(record) + "\n")
    (output / "manifest.json").write_text(
        json.dumps({"sequence_frame_count": 85, "input_sha256": hashes})
    )
    manifest, frames, error = validate_artifact(output, source)
    assert manifest is not None
    assert len(frames) == 85
    assert error is None
    (output / "class_masks/00084.png").unlink()
    assert validate_artifact(output, source) == (None, {}, "artifact_file_incomplete")


def test_reset_pauses_then_resets_and_never_auto_resumes(
    monkeypatch,
) -> None:
    calls = []

    def fake_upstream(self, service, path, *, body=None, token_file=None):
        del self, body, token_file
        calls.append((service, path))
        if path.startswith("/v1/public/snapshot"):
            return 200, {"snapshot_id": "run:protected:epoch-2:snapshot:0"}
        if path == "/health":
            return 200, {"epoch": 1, "startup_recovery_ready": False}
        return 200, {"plant_epoch": 2, "paused": True}

    monkeypatch.setattr(ConsoleHandler, "_json_upstream", fake_upstream)
    handler = object.__new__(ConsoleHandler)
    status, payload = handler._operator_action("reset", {})
    assert status == HTTPStatus.ACCEPTED
    assert calls[:2] == [
        ("simulator", "/v1/operator/pause?branch=protected"),
        ("simulator", "/v1/operator/reset?branch=protected"),
    ]
    assert not any(path.startswith("/v1/operator/resume") for _, path in calls)
    assert payload["state"] == "reset_in_progress"
    assert payload["control"]["resume_permitted"] is False


def test_resume_is_blocked_until_gate_epoch_and_recovery_are_ready(monkeypatch) -> None:
    handler = object.__new__(ConsoleHandler)
    monkeypatch.setattr(
        ConsoleHandler,
        "_operator_status",
        lambda self: {"resume_permitted": False, "state": "reset_in_progress"},
    )
    status, payload = handler._operator_action("resume", {})
    assert status == HTTPStatus.CONFLICT
    assert payload["error"] == "STARTUP_RECOVERY_NOT_READY"


def test_fault_requires_declared_id_and_boolean_enabled() -> None:
    handler = object.__new__(ConsoleHandler)
    handler.declared_fault_ids = frozenset({"slow-rudder"})
    status, payload = handler._operator_action(
        "fault", {"fault_id": "slow-rudder", "enabled": "false"}
    )
    assert status == HTTPStatus.BAD_REQUEST
    assert payload["error"] == "FAULT_ENABLED_MUST_BE_BOOLEAN"
    status, payload = handler._operator_action(
        "fault", {"fault_id": "undeclared", "enabled": False}
    )
    assert status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert payload["error"] == "FAULT_NOT_DECLARED"


def test_gate_acknowledgement_preserves_upstream_rejection(monkeypatch) -> None:
    handler = object.__new__(ConsoleHandler)
    monkeypatch.setattr(
        ConsoleHandler,
        "_json_upstream",
        lambda self, service, path, **kwargs: (200, {"accepted": False}),
    )
    monkeypatch.setattr(
        ConsoleHandler,
        "_operator_status",
        lambda self: {"resume_permitted": False, "state": "unavailable"},
    )
    status, payload = handler._operator_action("acknowledge", {})
    assert status == HTTPStatus.OK
    assert payload["accepted"] is False
