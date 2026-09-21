from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.http_api import SimulatorHTTPServer, SimulatorRuntime
from horizon_sim.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[3]


def _post(base: str, path: str, body: dict, token: str) -> tuple[int, dict]:
    request = Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=2.0) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def _get(base: str, path: str) -> dict:
    with urlopen(f"{base}{path}", timeout=2.0) as response:
        return json.load(response)


def _envelope(sim: AuthoritativeSimulator, sequence: int, expiry_ns: int) -> dict:
    return {
        "run_id": sim.run_id,
        "branch_id": sim.branch_id,
        "decision_id": f"http-decision-{sequence}",
        "command_id": f"http-command-{sequence}",
        "authority": "filtered_autonomy",
        "sequence": sequence,
        "epoch": sim.plant_epoch,
        "expires_simulation_time_s": sim.simulation_time_s + 1.0,
        "expires_monotonic_ns": expiry_ns,
        "command": {"heading_rad": 0.4, "speed_mps": 4.0},
    }


def _resume_body(sim: AuthoritativeSimulator, expiry_ns: int) -> dict:
    # Receiver-boundary fixture: production proof originates from gate status.
    return {"startup_recovery_certificate": {
        "decision_id": "test-recovery", "input_snapshot_id": "test-snapshot",
        "proposal_id": "test-proposal", "run_id": sim.run_id,
        "branch_id": sim.branch_id, "plant_epoch": sim.plant_epoch,
        "original_host_valid_until_ns": expiry_ns,
    }}


def test_real_http_queue_pause_resume_and_reset_close_expiry_gaps() -> None:
    simulator = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios" / "crossing_recoverable.json"),
        seed=22,
        run_id="http-deadline-run",
    )
    runtime = SimulatorRuntime(simulator, realtime=True)
    server = SimulatorHTTPServer(("127.0.0.1", 0), runtime, operator_token="operator-token")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    runtime.start()
    server_thread.start()
    try:
        # A request whose bytes arrive after the gate-side timeout is rejected
        # by the plant even though its simulation deadline is still ahead.
        status, expired = _post(
            base,
            "/v1/gate/command?branch=protected",
            _envelope(simulator, 0, time.monotonic_ns() - 1),
            simulator.gate_token,
        )
        assert status == 422
        assert "HOST_DEADLINE_EXPIRED" in expired["reason_codes"]

        # Holding the receiver lock models a queued handler. Its deadline
        # elapses before the handler can mutate the command state.
        queued_result: list[tuple[int, dict]] = []
        with runtime.lock:
            queued = _envelope(simulator, 0, time.monotonic_ns() + 60_000_000)
            worker = threading.Thread(
                target=lambda: queued_result.append(
                    _post(
                        base,
                        "/v1/gate/command?branch=protected",
                        queued,
                        simulator.gate_token,
                    )
                )
            )
            worker.start()
            time.sleep(0.10)
        worker.join(timeout=2.0)
        assert queued_result[0][0] == 422
        assert "HOST_DEADLINE_EXPIRED" in queued_result[0][1]["reason_codes"]

        # A valid command accepted while physics is paused still expires on
        # host monotonic time before the resumed plant applies another step.
        assert _post(base, "/v1/operator/pause", {}, "operator-token")[0] == 200
        active = _envelope(simulator, 0, time.monotonic_ns() + 150_000_000)
        assert _post(
            base,
            "/v1/gate/command?branch=protected",
            active,
            simulator.gate_token,
        )[0] == 200
        time.sleep(0.20)
        with runtime.lock:
            assert runtime.paused.is_set()
            assert simulator.active_command.command_id == (
                "plant-expiry-neutral:host_monotonic_deadline"
            )
        assert _post(base, "/v1/operator/resume", _resume_body(simulator, time.monotonic_ns() + 500_000_000), "operator-token")[0] == 200
        time.sleep(0.05)
        with runtime.lock:
            assert simulator.active_command.command_id == (
                "plant-expiry-neutral:host_monotonic_deadline"
            )

        # Reset advances epoch while preserving sequence. A previously unseen
        # higher-sequence command from the old epoch cannot revive afterwards.
        old = _envelope(simulator, 7, time.monotonic_ns() + 500_000_000)
        reset_status, reset = _post(
            base, "/v1/operator/reset", {}, "operator-token"
        )
        assert reset_status == 200
        assert reset["plant_epoch"] == old["epoch"] + 1
        snapshot = _get(base, "/v1/public/snapshot?branch=protected")
        observations = _get(base, "/v1/observations?branch=protected")
        assert f":epoch-{reset['plant_epoch']}:" in snapshot["snapshot_id"]
        assert observations["plant_epoch"] == reset["plant_epoch"]
        stale_status, stale = _post(
            base,
            "/v1/gate/command?branch=protected",
            old,
            simulator.gate_token,
        )
        assert stale_status == 422
        assert "STALE_PLANT_EPOCH" in stale["reason_codes"]
    finally:
        runtime.stop()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)


def test_resume_rechecks_proof_after_queue_delay_and_reset():
    clock = [1_000_000_000]
    sim = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios/crossing_recoverable.json"),
        seed=22, run_id="resume-proof", monotonic_ns=lambda: clock[0],
    )
    runtime = SimulatorRuntime(sim, realtime=False)
    runtime.paused.set()
    server = SimulatorHTTPServer(("127.0.0.1", 0), runtime, operator_token="operator-token")
    base = f"http://127.0.0.1:{server.server_port}"
    server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    server_thread.start()
    try:
        result = []
        with runtime.lock:
            body = _resume_body(sim, clock[0] + 1)
            worker = threading.Thread(target=lambda: result.append(_post(base, "/v1/operator/resume", body, "operator-token")))
            worker.start()
            clock[0] += 1
        worker.join(timeout=2)
        assert result[0][0] == 409
        assert result[0][1]["error"] == "STARTUP_RECOVERY_CERTIFICATE_EXPIRED"
        assert runtime.paused.is_set()

        body = _resume_body(sim, clock[0] + 1_000_000_000)
        sim.reset()
        assert _post(base, "/v1/operator/resume", body, "operator-token")[1]["error"] == "STARTUP_RECOVERY_EPOCH_MISMATCH"
        for field, invalid in (("plant_epoch", True), ("original_host_valid_until_ns", False), ("decision_id", ""), ("run_id", "other-run"), ("branch_id", "other-branch")):
            body = _resume_body(sim, clock[0] + 1_000_000_000)
            body["startup_recovery_certificate"][field] = invalid
            assert _post(base, "/v1/operator/resume", body, "operator-token")[0] == 409
            assert runtime.paused.is_set()
        assert _post(base, "/v1/operator/resume", {}, "operator-token")[0] == 409
        assert _post(base, "/v1/operator/resume", _resume_body(sim, clock[0] + 1), "operator-token")[0] == 200
        assert not runtime.paused.is_set()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)


def test_paused_http_reset_streams_complete_new_epoch_sensor_readiness() -> None:
    simulator = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios" / "crossing_recoverable.json"),
        seed=23,
        run_id="paused-reset-readiness",
    )
    runtime = SimulatorRuntime(simulator, realtime=True)
    server = SimulatorHTTPServer(("127.0.0.1", 0), runtime, operator_token="operator-token")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    runtime.start()
    server_thread.start()
    try:
        assert _post(base, "/v1/operator/pause", {}, "operator-token")[0] == 200
        reset_status, reset = _post(
            base, "/v1/operator/reset", {}, "operator-token"
        )
        assert reset_status == 200
        epoch = reset["plant_epoch"]
        deadline = time.monotonic() + 1.0
        batch = {"observations": []}
        while time.monotonic() < deadline:
            batch = _get(base, "/v1/observations?branch=protected")
            sources = {item["source_id"] for item in batch["observations"]}
            if {"gnss", "imu", "radar", "actuator", "actuator_setpoint"}.issubset(
                sources
            ):
                break
            time.sleep(0.01)
        snapshot = _get(base, "/v1/public/snapshot?branch=protected")
        health = _get(base, "/health")

        assert batch["plant_epoch"] == epoch
        assert snapshot["simulation_time_s"] == 0.0
        assert snapshot["tick_index"] > 0
        assert simulator.tick_index == 0
        assert health["paused"] is True
        assert health["physical_tick_index"] == 0
        assert health["observation_tick_index"] >= snapshot["tick_index"]
        assert health["active_authority"] == "plant_startup_passive"
        assert all(f":epoch-{epoch}:" in item["observation_id"] for item in batch["observations"])
        assert any(
            item["payload"]["_simulator"]["capture_clock"]
            == "host_cadence_while_physics_paused"
            for item in batch["observations"]
        )
    finally:
        runtime.stop()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)
