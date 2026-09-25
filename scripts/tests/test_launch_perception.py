from __future__ import annotations

import hashlib
import io
from pathlib import Path
import json
import sys
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import launch  # noqa: E402
import perception_runtime  # noqa: E402


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    repository = tmp_path / "repository"
    data = tmp_path / "data"
    config_dir = repository / "configs/perception"
    config_dir.mkdir(parents=True)
    sequence_id = "kope81-00-test-sequence"
    split = {
        "schema_version": "horizon.perception-splits.v1",
        "development": {"sequences": [sequence_id]},
    }
    split_path = config_dir / "modd2-splits.json"
    split_path.write_text(json.dumps(split, sort_keys=True) + "\n")
    split_sha = hashlib.sha256(split_path.read_bytes()).hexdigest()

    (data / "sources/WaSR-T").mkdir(parents=True)
    weights = data / "weights/wasrt_mastr1325.pth"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"checkpoint-fixture")
    sequence = data / "datasets/modd2/video/video_data" / sequence_id
    frames = sequence / "frames"
    frames.mkdir(parents=True)
    (frames / "0001L.jpg").write_bytes(b"left-frame")
    (frames / "0001R.jpg").write_bytes(b"right-frame")
    calibration = sequence / "calibration.yaml"
    calibration.write_text("camera: fixture\n")
    calibration_sha = hashlib.sha256(calibration.read_bytes()).hexdigest()

    value = {
        "schema_version": "horizon.recorded-camera-live.v1",
        "mode": perception_runtime.MODE,
        "source": {
            "dataset": "MODD2",
            "sequence": sequence_id,
            "camera": "left",
            "partition": "development",
            "timestamp_source": "synthetic_10hz_order_only",
            "split_manifest_sha256": split_sha,
        },
        "camera_geometry": {
            "version": "fixture-intrinsics-only",
            "source_calibration": {"sha256": calibration_sha},
            "metric_projection": {"status": "unavailable", "contacts_emitted": False},
        },
        "runtime": {
            "cadence_s": 0.1,
            "queue_capacity": 2,
            "ttl_s": 3.0,
            "drop_policy": "drop_oldest_queued_frame",
            "publication_retry_count": 0,
        },
    }
    config = config_dir / "recorded-live.json"
    config.write_text(json.dumps(value, indent=2) + "\n")
    return repository, data, value


def test_default_launch_order_and_command_remain_seven_service_baseline(tmp_path: Path) -> None:
    assert launch.launch_order(perception_enabled=False) == (
        "simulator",
        "decision_ai",
        "collector",
        "fusion",
        "gate",
        "assurance",
        "console",
    )
    assert launch.perception_command(
        None,
        host="127.0.0.1",
        port=None,
        collector_port=8105,
        run_id="baseline",
        external_data_root=tmp_path,
    ) is None
    assert launch.launch_order(perception_enabled=True) == (
        "simulator",
        "decision_ai",
        "collector",
        "perception",
        "fusion",
        "gate",
        "assurance",
        "console",
    )


def test_valid_config_resolves_one_bounded_left_camera_source(tmp_path: Path, monkeypatch) -> None:
    repository, data, _ = _write_fixture(tmp_path)
    config = perception_runtime.load_perception_config(
        repository / "configs/perception/recorded-live.json",
        repository_root=repository,
        external_data_root=data,
    )
    monkeypatch.setattr(launch, "ROOT", repository)
    command = launch.perception_command(
        config,
        host="127.0.0.1",
        port=49123,
        collector_port=8105,
        run_id="recorded-camera-run",
        external_data_root=data,
    )

    assert config.frame_count == 1
    assert config.partition == "development"
    assert command is not None
    assert command[0] == str(repository / ".venv/bin/python")
    assert command[1] == str(repository / "scripts/perception_runtime.py")
    assert command[command.index("--port") + 1] == "49123"
    assert command[command.index("--collector-url") + 1] == "http://127.0.0.1:8105"
    public = config.public_identity()
    assert public["metric_contacts_usable"] is False
    assert public["camera_free_space_usable"] is False
    assert public["calibrated_risk_band"] == "unknown"
    assert not any(str(data) in str(value) for value in public.values())


@pytest.mark.parametrize("mode", ["shadow_only", "simulation_warning"])
def test_h5_config_pins_reference_and_launches_selected_mode(tmp_path: Path, mode: str) -> None:
    repository, data, value = _write_fixture(tmp_path)
    reference = data / "references/neural-health/h5.json"
    reference.parent.mkdir(parents=True)
    reference.write_text(json.dumps({
        "method_id": "H5",
        "version": "h5-fixture-v1",
        "artifact_hash": "a" * 64,
    }))
    value["health_monitor"] = {
        "method": "H5",
        "mode": mode,
        "simulation_warning": {"threshold": 2.731332008015018, "reference_hash": "a" * 64},
        "reference_artifact": {
            "data_relative_path": "references/neural-health/h5.json",
            "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        },
    }
    config_path = repository / "configs/perception/recorded-live.json"
    config_path.write_text(json.dumps(value, indent=2) + "\n")

    config = perception_runtime.load_perception_config(
        config_path, repository_root=repository, external_data_root=data
    )
    command = config.command(
        python=repository / ".venv/bin/python",
        collector_url="http://127.0.0.1:8105",
        run_id="h5-product",
    )

    assert config.method == "H5"
    assert command[command.index("--method") + 1] == "H5"
    assert command[command.index("--reference-artifact") + 1] == str(reference)
    assert config.public_identity()["reference_version"] == "h5-fixture-v1"
    assert config.public_identity()["health_mode"] == mode
    if mode == "simulation_warning":
        assert config.public_identity()["simulation_warning_threshold"] == 2.731332008015018
        for changes in ({"threshold": -1}, {"reference_hash": "b" * 64}):
            invalid = json.loads(json.dumps(value))
            invalid["health_monitor"]["simulation_warning"].update(changes)
            config_path.write_text(json.dumps(invalid))
            with pytest.raises(ValueError, match="simulation warning"):
                perception_runtime.load_perception_config(
                    config_path, repository_root=repository, external_data_root=data
                )


def test_h5_config_fails_closed_when_reference_is_missing(tmp_path: Path) -> None:
    repository, data, value = _write_fixture(tmp_path)
    value["health_monitor"] = {
        "method": "H5",
        "reference_artifact": {
            "data_relative_path": "references/neural-health/missing.json",
            "sha256": "0" * 64,
        },
    }
    config_path = repository / "configs/perception/recorded-live.json"
    config_path.write_text(json.dumps(value, indent=2) + "\n")

    with pytest.raises(ValueError, match="missing or has the wrong SHA-256"):
        perception_runtime.load_perception_config(
            config_path, repository_root=repository, external_data_root=data
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["camera_geometry"]["metric_projection"].update(contacts_emitted=True), "metric contacts"),
        (lambda value: value["runtime"].update(publication_retry_count=1), "retries"),
        (lambda value: value["runtime"].update(queue_capacity=0), "queue_capacity"),
        (lambda value: value["source"].update(partition="heldout"), "development-only"),
        (lambda value: value["source"].update(sequence="../escape"), "safe path"),
    ],
)
def test_config_authority_and_bounds_fail_closed(
    tmp_path: Path, mutation, message: str
) -> None:
    repository, data, value = _write_fixture(tmp_path)
    mutation(value)
    config = repository / "configs/perception/recorded-live.json"
    config.write_text(json.dumps(value) + "\n")
    with pytest.raises(ValueError, match=message):
        perception_runtime.load_perception_config(
            config,
            repository_root=repository,
            external_data_root=data,
        )


def test_invalid_config_stops_launcher_before_run_directory_or_process(
    tmp_path: Path, monkeypatch
) -> None:
    empty_data = tmp_path / "missing-data"
    empty_data.mkdir()
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("HORIZON_DATA_ROOT", str(empty_data))
    monkeypatch.setenv("HORIZON_RUNS_DIR", str(runs))
    monkeypatch.setattr(
        launch.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("process started before config validation")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "launch.py",
            "--run-id",
            "invalid-perception-startup",
            "--perception-config",
            "configs/perception/recorded-camera-live.json",
        ],
    )

    with pytest.raises(SystemExit) as error:
        launch.main()
    assert error.value.code == 2
    assert list(runs.iterdir()) == []


def test_public_readiness_and_diagnostics_expose_no_external_paths(tmp_path: Path) -> None:
    repository, data, _ = _write_fixture(tmp_path)
    config = perception_runtime.load_perception_config(
        repository / "configs/perception/recorded-live.json",
        repository_root=repository,
        external_data_root=data,
    )
    state = perception_runtime.RuntimeState(config)
    server = perception_runtime.RuntimeHTTPServer(("127.0.0.1", 0), state)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as starting:
            urlopen(f"{base}/health", timeout=0.5)
        assert starting.value.code == 503
        state.update(
            phase="running",
            ready=True,
            observed_sources=[perception_runtime.CAMERA_SOURCE, perception_runtime.HEALTH_SOURCE],
            fresh_sources=[perception_runtime.CAMERA_SOURCE, perception_runtime.HEALTH_SOURCE],
        )
        with urlopen(f"{base}/health", timeout=0.5) as response:
            health = json.load(response)
        with urlopen(f"{base}/v1/diagnostics", timeout=0.5) as response:
            diagnostics = json.load(response)
        assert health == {
            "status": "ready",
            "ready": True,
            "source_exhausted": False,
            "mode": perception_runtime.MODE,
        }
        assert diagnostics["metric_contacts_usable"] is False
        assert diagnostics["camera_free_space_usable"] is False
        assert str(data) not in json.dumps(diagnostics)
        assert str(repository) not in json.dumps(diagnostics)
        state.update(phase="complete", ready=False, source_exhausted=True)
        with pytest.raises(HTTPError) as exhausted:
            urlopen(f"{base}/health", timeout=0.5)
        assert exhausted.value.code == 503
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=1.0)


def test_child_result_is_reduced_to_bounded_diagnostics() -> None:
    value = {
        "captured": 4,
        "processed": 3,
        "published_observations": 6,
        "queue_drops": 1,
        "errors": ["private-path:/tmp/source.jpg"],
        "weights_sha256": "secret-like-detail",
        "source_exhausted": True,
    }
    bounded = perception_runtime._bounded_result(value)
    assert bounded == {
        "captured": 4,
        "processed": 3,
        "published_observations": 6,
        "queue_drops": 1,
        "source_exhausted": True,
    }


def test_successful_child_without_published_observations_fails_closed(
    tmp_path: Path,
) -> None:
    repository, data, _ = _write_fixture(tmp_path)
    config = perception_runtime.load_perception_config(
        repository / "configs/perception/recorded-live.json",
        repository_root=repository,
        external_data_root=data,
    )
    state = perception_runtime.RuntimeState(config)

    class Child:
        def poll(self):
            return 0

    output = io.BytesIO(
        json.dumps(
            {
                "captured": 1,
                "processed": 0,
                "published_observations": 0,
                "source_exhausted": True,
            }
        ).encode()
    )
    perception_runtime.monitor_child(
        Child(), output, state, "http://127.0.0.1:1/v1/diagnostics", threading.Event()
    )

    result = state.snapshot()
    assert result["phase"] == "failed"
    assert result["ready"] is False
    assert result["failure"] == {
        "code": "PERCEPTION_NO_OBSERVATIONS_PUBLISHED",
        "exit_code": 0,
    }


def test_supervisor_shutdown_terminates_child_before_kill() -> None:
    class Child:
        def __init__(self) -> None:
            self.returncode = None
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            assert timeout == 5.0
            self.returncode = 0
            return 0

        def kill(self):
            self.killed = True

    child = Child()
    perception_runtime._stop_child(child)
    assert child.terminated is True
    assert child.killed is False
    assert child.returncode == 0
