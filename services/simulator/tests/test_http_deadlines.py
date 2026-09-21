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
        assert _post(base, "/v1/operator/resume", {}, "operator-token")[0] == 200
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
