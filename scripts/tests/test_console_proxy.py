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
    copy_upstream_body,
    load_artifact_frames,
    load_demo_catalog,
    load_demo_run,
    resolve_public_route,
    validate_artifact,
)


def test_live_traffic_proxy_route_is_narrowly_allowlisted() -> None:
    assert resolve_public_route("/api/collector/v1/traffic/snapshot") == (
        "collector",
        "/v1/traffic/snapshot",
    )
    assert resolve_public_route("/api/collector/v1/traffic/raw") is None


def _demo_fixture(root: Path, run_id: str = "unsafe-route-v1") -> tuple[Path, dict, dict]:
    directory = root / run_id
    directory.mkdir(parents=True)
    replay = {
        "schema_version": "horizon.demo-replay.v1",
        "run_id": run_id,
        "timeline": {"frames": []},
    }
    replay_bytes = (json.dumps(replay, separators=(",", ":")) + "\n").encode()
    manifest = {
        "schema_version": "horizon.demo-manifest.v1",
        "run_id": run_id,
        "title": "Unsafe course · safety takeover",
        "source_dirty": False,
        "replay_sha256": hashlib.sha256(replay_bytes).hexdigest(),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    (directory / "replay.json").write_bytes(replay_bytes)
    return directory, manifest, replay


def test_demo_catalog_and_run_are_exact_hash_checked_allowlisted_json(tmp_path: Path) -> None:
    directory, manifest, replay = _demo_fixture(tmp_path)

    assert load_demo_run(tmp_path, "../unsafe-route-v1") is None
    assert load_demo_run(tmp_path, "unsafe-route-v1") == (manifest, replay)
    assert load_demo_catalog(tmp_path) == {
        "schema_version": "horizon.demo-catalog.v1",
        "runs": [
            {
                **manifest,
                "replay_url": "/api/demo/runs/unsafe-route-v1",
            }
        ],
    }

    (directory / "replay.json").write_text("{}\n")
    assert load_demo_run(tmp_path, "unsafe-route-v1") is None
    assert load_demo_catalog(tmp_path)["runs"] == []


def test_proxy_does_not_emit_a_second_http_status_after_stream_headers(monkeypatch) -> None:
    class Response:
        status = 200
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def readline(self):
            raise TimeoutError

    class Destination:
        closed = False

        def write(self, _value):
            return None

        def flush(self):
            return None

    class Handler:
        upstreams = {"simulator": "http://127.0.0.1:1"}
        headers = {"Accept": "text/event-stream"}
        wfile = Destination()
        close_connection = False

        def __init__(self):
            self.statuses = []
            self.json_calls = []

        def send_response(self, status):
            self.statuses.append(status)

        def send_header(self, *_args):
            return None

        def end_headers(self):
            return None

        def _json(self, *args):
            self.json_calls.append(args)

    monkeypatch.setattr("console_proxy.urlopen", lambda *_args, **_kwargs: Response())
    handler = Handler()

    ConsoleHandler._proxy_get(handler, "simulator", "/v1/public/stream")

    assert handler.statuses == [200]
    assert handler.json_calls == []
    assert handler.close_connection is True


def test_sse_proxy_flushes_each_line_without_waiting_for_a_large_read() -> None:
    class IncrementalStream:
        headers = {"Content-Type": "text/event-stream; charset=utf-8"}

        def __init__(self) -> None:
            self.lines = iter(
                [b"event: snapshot\n", b'data: {"tick_index":1}\n', b"\n", b""]
            )

        def readline(self) -> bytes:
            return next(self.lines)

        def read(self, _size: int) -> bytes:
            raise AssertionError("SSE forwarding must not wait for a chunked read")

    class Destination:
        def __init__(self) -> None:
            self.parts: list[bytes] = []
            self.flushes = 0

        def write(self, value: bytes) -> None:
            self.parts.append(value)

        def flush(self) -> None:
            self.flushes += 1

    destination = Destination()
    copy_upstream_body(IncrementalStream(), destination)

    assert b"".join(destination.parts) == (
        b"event: snapshot\n" b'data: {"tick_index":1}\n' b"\n"
    )
    assert destination.flushes == 3


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


def test_console_health_exposes_the_launcher_selected_candidate(monkeypatch) -> None:
    responses = []
    handler = object.__new__(ConsoleHandler)
    handler.path = "/health"
    monkeypatch.setattr(ConsoleHandler, "candidate_id", "A3")
    monkeypatch.setattr(
        ConsoleHandler,
        "_json",
        lambda self, status, value: responses.append((status, value)),
    )

    handler.do_GET()

    assert responses == [
        (
            HTTPStatus.OK,
            {"status": "ok", "service": "horizon-console", "candidate_id": "A3"},
        )
    ]


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
            return 200, {
                "epoch": 1,
                "startup_recovery_ready": False,
                "startup_recovery_certificate": None,
            }
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
    handler.resume_readiness_timeout_s = 0.0
    monkeypatch.setattr(
        ConsoleHandler,
        "_operator_status",
        lambda self: {"resume_permitted": False, "state": "reset_in_progress"},
    )
    status, payload = handler._operator_action("resume", {})
    assert status == HTTPStatus.CONFLICT
    assert payload["error"] == "STARTUP_RECOVERY_NOT_READY"


def test_explicit_resume_waits_for_bounded_recovery_window_then_calls_plant_once(
    monkeypatch,
) -> None:
    handler = object.__new__(ConsoleHandler)
    handler.resume_readiness_timeout_s = 1.0
    handler.resume_readiness_poll_s = 0.0
    certificate = {
        "run_id": "run-7",
        "branch_id": "protected",
        "decision_id": "recovery-7",
        "input_snapshot_id": "snapshot-7",
        "proposal_id": "proposal-7",
        "plant_epoch": 2,
        "original_host_valid_until_ns": 4_000_000_000,
    }
    statuses = iter(
        [
            {"resume_permitted": False, "state": "reset_in_progress"},
            {"resume_permitted": False, "state": "reset_in_progress"},
            {
                "resume_permitted": True,
                "state": "ready",
                "startup_recovery_certificate": certificate,
            },
            {"resume_permitted": True, "state": "ready"},
        ]
    )
    calls = []

    monkeypatch.setattr(ConsoleHandler, "_operator_status", lambda self: next(statuses))

    def fake_upstream(self, service, path, *, body=None, token_file=None):
        del self, token_file
        calls.append((service, path, body))
        return 200, {"accepted": True, "paused": False}

    monkeypatch.setattr(ConsoleHandler, "_json_upstream", fake_upstream)
    status, payload = handler._operator_action("resume", {})
    assert status == HTTPStatus.OK
    assert payload["accepted"] is True
    assert calls == [
        (
            "simulator",
            "/v1/operator/resume?branch=protected",
            {"startup_recovery_certificate": certificate},
        )
    ]


def test_restart_resets_then_resumes_the_demo(monkeypatch) -> None:
    handler = object.__new__(ConsoleHandler)
    certificate = {
        "run_id": "run-8",
        "branch_id": "protected",
        "decision_id": "recovery-8",
        "input_snapshot_id": "snapshot-8",
        "proposal_id": "proposal-8",
        "plant_epoch": 3,
        "original_host_valid_until_ns": 5_000_000_000,
    }
    monkeypatch.setattr(
        ConsoleHandler,
        "_operator_status",
        lambda self: {
            "resume_permitted": True,
            "state": "ready",
            "startup_recovery_certificate": certificate,
        },
    )
    calls = []

    def fake_upstream(self, service, path, *, body=None, token_file=None):
        del self, token_file
        calls.append((service, path, body))
        return 200, {"accepted": True}

    monkeypatch.setattr(ConsoleHandler, "_json_upstream", fake_upstream)
    status, payload = handler._operator_action("restart", {})

    assert status == HTTPStatus.OK
    assert payload["accepted"] is True
    assert payload["state"] == "running"
    assert [path for _, path, _ in calls] == [
        "/v1/operator/pause?branch=protected",
        "/v1/operator/reset?branch=protected",
        "/v1/operator/resume?branch=protected",
    ]


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


def test_rate_is_bounded_and_forwarded_to_the_simulator(monkeypatch) -> None:
    handler = object.__new__(ConsoleHandler)
    handler.simulator_operator_token_file = Path("operator.token")
    monkeypatch.setattr(
        ConsoleHandler,
        "_json_upstream",
        lambda self, service, path, **kwargs: (200, {"time_scale": kwargs["body"]["multiplier"]}),
    )
    monkeypatch.setattr(ConsoleHandler, "_operator_status", lambda self: {"state": "ready"})
    status, payload = handler._operator_action("rate", {"multiplier": 4})
    assert status == HTTPStatus.OK
    assert payload["accepted"] is True
    status, payload = handler._operator_action("rate", {"multiplier": 3})
    assert status == HTTPStatus.BAD_REQUEST
    assert payload["error"] == "RATE_MULTIPLIER_MUST_BE_1_2_OR_4"


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
