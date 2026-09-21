#!/usr/bin/env python3
"""Validate and execute one centrally authorized, finite Modal attempt."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess

from compute_ledger import begin, default_ledger_path, finish_attempt, initialize, reservation, validate

ROOT = Path(__file__).resolve().parents[1]
MODAL_CLI = Path("/Users/w3joe/Desktop/2026_sdth/horizon-tools/bin/modal")


def validate_spec(spec: dict, reservation_record: dict) -> None:
    limits = spec["limits"]
    checks = {
        "reservation upper bound": spec["cost"]["authorized_upper_bound_usd"] == reservation_record["upper_bound_usd"],
        "one L4": limits["gpu_type"] == "L4" and limits["gpu_count"] == 1,
        "global GPU ceiling": limits["gpu_count"] <= limits["max_total_gpu_count"] <= 10,
        "single container": limits["max_containers"] == 1 and limits["min_containers"] == 0,
        "single attempt": limits["retries"] == 0 and limits["total_attempt_cap"] == 1,
        "finite startup": 0 < limits["startup_timeout_s"] <= 900,
        "finite execution": 0 < limits["execution_timeout_s"] <= 1200,
        "finite controller wall clock": 0 < limits["controller_wall_timeout_s"] <= 2250,
        "CPU hard limit": limits["physical_cpu"]["request"] == limits["physical_cpu"]["limit"] == 4.0,
        "RAM hard limit": limits["memory_gib"]["request"] == limits["memory_gib"]["limit"] == 16.0,
        "bounded input": spec["input"]["ordered_frame_count"] == limits["input_frame_cap"] == 85,
        "bounded output": limits["output_bytes_cap"] <= 157_286_400,
        "offline execution": spec["input"]["network_during_gpu_execution"] == "blocked",
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError("job violates finite controls: " + ", ".join(failed))


def modal(command: list[str], environment: dict[str, str], timeout_s: int = 120) -> int:
    process = subprocess.Popen(
        [str(MODAL_CLI), *command],
        cwd=ROOT,
        env=environment,
        start_new_session=True,
    )
    try:
        return process.wait(timeout=timeout_s)
    except (KeyboardInterrupt, subprocess.TimeoutExpired):
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        raise


def stop_remote_app(spec: dict, environment: dict[str, str]) -> None:
    try:
        subprocess.run(
            [str(MODAL_CLI), "app", "stop", spec["modal_app_name"], "--yes"],
            cwd=ROOT,
            env=environment,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def delete_volume(spec: dict, environment: dict[str, str]) -> bool:
    try:
        result = subprocess.run(
            [str(MODAL_CLI), "volume", "delete", spec["modal_volume_name"], "--yes", "--allow-missing"],
            cwd=ROOT,
            env=environment,
            timeout=60,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def validate_download(spec: dict) -> None:
    output = Path(spec["output"]["local_path"])
    required = [output / "manifest.json", output / "features.jsonl", output / "masks"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Modal output is incomplete: " + ", ".join(missing))
    masks = list((output / "masks").glob("*.png"))
    if len(masks) != spec["output"]["expected_mask_count"]:
        raise RuntimeError(f"expected 85 masks, found {len(masks)}")
    size = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    if size > spec["limits"]["output_bytes_cap"]:
        raise RuntimeError(f"downloaded output exceeds cap: {size} bytes")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reservation", required=True)
    parser.add_argument("--ledger", type=Path, default=default_ledger_path())
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    initialize(args.ledger)
    ledger = json.loads(args.ledger.read_text())
    validate(ledger)
    item = reservation(ledger, args.reservation)
    spec_path = ROOT / item["job_spec"]
    spec = json.loads(spec_path.read_text())
    validate_spec(spec, item)
    entrypoint = ROOT / spec["modal_entrypoint"]
    if not args.execute:
        print(json.dumps({"valid": True, "reservation": item, "entrypoint_present": entrypoint.is_file()}, indent=2))
        return 0
    if not entrypoint.is_file():
        raise SystemExit(f"refusing cloud run: reviewed A07 entrypoint is missing: {entrypoint}")
    if not MODAL_CLI.is_file():
        raise SystemExit(f"refusing cloud run: isolated Modal CLI is missing: {MODAL_CLI}")

    environment = os.environ.copy()
    environment["HORIZON_MODAL_JOB_SPEC"] = str(spec_path)
    environment["HORIZON_DATA_ROOT"] = str(Path(spec["input"]["source_path"]).parents[1])
    environment["HORIZON_MODAL_VOLUME"] = spec["modal_volume_name"]
    output = Path(spec["output"]["local_path"])
    if output.exists():
        raise SystemExit(f"refusing cloud run: local output path already exists: {output}")

    begin(args.ledger, args.reservation)
    try:
        create_rc = modal(["volume", "create", spec["modal_volume_name"]], environment)
        if create_rc:
            raise RuntimeError("failed to create the dedicated bounded output Volume")
        run_rc = modal(
            ["run", str(entrypoint)],
            environment,
            timeout_s=spec["limits"]["controller_wall_timeout_s"],
        )
        stop_remote_app(spec, environment)
        if run_rc:
            raise RuntimeError(f"Modal run exited with status {run_rc}")
        output.parent.mkdir(parents=True, exist_ok=True)
        get_rc = modal(
            ["volume", "get", spec["modal_volume_name"], spec["output"]["remote_path"], str(output)],
            environment,
            timeout_s=300,
        )
        if get_rc:
            raise RuntimeError("failed to download bounded output artifacts")
        validate_download(spec)
    except BaseException as error:
        stop_remote_app(spec, environment)
        volume_deleted = delete_volume(spec, environment)
        finish_attempt(
            args.ledger,
            args.reservation,
            None,
            f"{type(error).__name__}; provider termination requested; volume_deleted={volume_deleted}; reconcile usage",
        )
        raise
    if not delete_volume(spec, environment):
        finish_attempt(args.ledger, args.reservation, None, "run complete; Volume deletion failed; reconcile and clean up")
        raise SystemExit("run completed but dedicated Volume deletion failed")
    finish_attempt(
        args.ledger,
        args.reservation,
        None,
        "run and artifact download complete; remote app stopped and Volume deleted; reconcile inclusive charge",
    )
    print("Modal attempt finished; reconcile the inclusive provider charge before another reservation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
