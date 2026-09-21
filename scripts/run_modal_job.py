#!/usr/bin/env python3
"""Validate and execute one centrally authorized, finite Modal attempt."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from typing import Any

from compute_ledger import begin, default_ledger_path, finish_attempt, initialize, reservation, validate

ROOT = Path(__file__).resolve().parents[1]
MODAL_CLI = Path("/Users/w3joe/Desktop/2026_sdth/horizon-tools/bin/modal")
APP_ID_PATTERN = re.compile(r"\bap-[A-Za-z0-9]{8,}\b")


@dataclass(frozen=True)
class ModalRunResult:
    returncode: int
    app_ids: frozenset[str]
    output: str


class ModalRunInterrupted(BaseException):
    def __init__(self, reason: str, app_ids: set[str], output: str):
        super().__init__(reason)
        self.reason = reason
        self.app_ids = app_ids
        self.output = output


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


def terminate_local_process(process: subprocess.Popen[Any]) -> None:
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def modal(command: list[str], environment: dict[str, str], timeout_s: int = 120) -> int:
    process = subprocess.Popen(
        [str(MODAL_CLI), *command], cwd=ROOT, env=environment, start_new_session=True
    )
    try:
        return process.wait(timeout=timeout_s)
    except (KeyboardInterrupt, subprocess.TimeoutExpired):
        terminate_local_process(process)
        raise


def run_modal(entrypoint: Path, environment: dict[str, str], timeout_s: int) -> ModalRunResult:
    process = subprocess.Popen(
        [str(MODAL_CLI), "run", str(entrypoint)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout_s)
    except (KeyboardInterrupt, subprocess.TimeoutExpired) as error:
        terminate_local_process(process)
        output, _ = process.communicate()
        print(output, end="")
        raise ModalRunInterrupted(type(error).__name__, set(APP_ID_PATTERN.findall(output)), output) from error
    print(output, end="")
    return ModalRunResult(process.returncode, frozenset(APP_ID_PATTERN.findall(output)), output)


def list_apps(environment: dict[str, str]) -> dict[str, dict[str, Any]] | None:
    try:
        result = subprocess.run(
            [str(MODAL_CLI), "app", "list", "--json"],
            cwd=ROOT,
            env=environment,
            timeout=30,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            return None
        rows = json.loads(result.stdout)
        return {row["app_id"]: row for row in rows if isinstance(row, dict) and "app_id" in row}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def resolve_app_id(
    captured_ids: set[str],
    before: dict[str, dict[str, Any]] | None,
    after: dict[str, dict[str, Any]] | None,
    expected_name: str,
) -> str | None:
    if len(captured_ids) == 1:
        return next(iter(captured_ids))
    if after is None:
        return None
    prior = set(before or {})
    candidates = {
        app_id
        for app_id, row in after.items()
        if app_id not in prior and row.get("description") == expected_name
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def stop_and_verify(app_id: str | None, environment: dict[str, str]) -> str:
    if app_id is None:
        return "unknown_no_app_id"
    try:
        stop = subprocess.run(
            [str(MODAL_CLI), "app", "stop", app_id, "--yes"],
            cwd=ROOT,
            env=environment,
            timeout=60,
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown_stop_command_failed"
    for _ in range(30):
        apps = list_apps(environment)
        if apps is None:
            time.sleep(2)
            continue
        row = apps.get(app_id)
        if row is None or (row.get("state") == "stopped" and str(row.get("tasks", "0")) == "0"):
            return "verified_stopped"
        time.sleep(2)
    return f"unknown_stop_rc_{stop.returncode}"


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
    required = [output / path.rstrip("/") for path in spec["output"]["required"]]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Modal output is incomplete: " + ", ".join(missing))
    class_masks = list((output / "class_masks").glob("*.png"))
    previews = list((output / "mask_previews").glob("*.png"))
    expected_masks = spec["output"]["expected_class_mask_count"]
    expected_previews = spec["output"]["expected_preview_count"]
    if len(class_masks) != expected_masks:
        raise RuntimeError(f"expected {expected_masks} class masks, found {len(class_masks)}")
    if len(previews) != expected_previews:
        raise RuntimeError(f"expected {expected_previews} mask previews, found {len(previews)}")
    size = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    if size > spec["limits"]["output_bytes_cap"]:
        raise RuntimeError(f"downloaded output exceeds cap: {size} bytes")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(str(item.relative_to(path)).encode())
        digest.update(b"\0")
        digest.update(sha256_file(item).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def write_run_metadata(spec: dict, app_id: str) -> None:
    output = Path(spec["output"]["local_path"])
    spec_path = ROOT / "infra/modal/jobs" / f"{spec['job_id']}.json"
    entrypoint = ROOT / spec["modal_entrypoint"]
    metadata = {
        "job_id": spec["job_id"],
        "reservation_id": spec["reservation_id"],
        "repository_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "modal_app_id": app_id,
        "limits": spec["limits"],
        "code": {
            "entrypoint": spec["modal_entrypoint"],
            "entrypoint_sha256": sha256_file(entrypoint),
            "job_spec_sha256": sha256_file(spec_path),
        },
        "artifacts": {
            "manifest_sha256": sha256_file(output / "manifest.json"),
            "features_sha256": sha256_file(output / "features.jsonl"),
            "class_masks_tree_sha256": sha256_tree(output / "class_masks"),
            "mask_previews_tree_sha256": sha256_tree(output / "mask_previews"),
        },
    }
    (output / "platform-run.json").write_text(json.dumps(metadata, indent=2) + "\n")


def execute_job(
    ledger_path: Path,
    reservation_id: str,
    spec: dict,
    entrypoint: Path,
    environment: dict[str, str],
) -> None:
    output = Path(spec["output"]["local_path"])
    before = list_apps(environment)
    volume_created = False
    app_id: str | None = None
    termination = "not_started"
    begin(ledger_path, reservation_id)
    try:
        create_rc = modal(["volume", "create", spec["modal_volume_name"]], environment)
        if create_rc:
            raise RuntimeError("failed to create the dedicated bounded output Volume")
        volume_created = True
        captured_ids: set[str] = set()
        try:
            run_result = run_modal(
                entrypoint, environment, spec["limits"]["controller_wall_timeout_s"]
            )
            captured_ids.update(run_result.app_ids)
        except ModalRunInterrupted as interrupted:
            captured_ids.update(interrupted.app_ids)
            after = list_apps(environment)
            app_id = resolve_app_id(captured_ids, before, after, spec["modal_app_name"])
            termination = stop_and_verify(app_id, environment)
            raise RuntimeError(f"Modal run interrupted: {interrupted.reason}") from interrupted
        after = list_apps(environment)
        app_id = resolve_app_id(captured_ids, before, after, spec["modal_app_name"])
        termination = stop_and_verify(app_id, environment)
        if run_result.returncode:
            raise RuntimeError(f"Modal run exited with status {run_result.returncode}")
        if termination != "verified_stopped":
            raise RuntimeError(f"remote app termination is unverified: {termination}")
        output.parent.mkdir(parents=True, exist_ok=True)
        get_rc = modal(
            ["volume", "get", spec["modal_volume_name"], spec["output"]["remote_path"], str(output)],
            environment,
            timeout_s=300,
        )
        if get_rc:
            raise RuntimeError("failed to download bounded output artifacts")
        validate_download(spec)
        if app_id is None:
            raise RuntimeError("cannot write run metadata without an exact provider app ID")
        write_run_metadata(spec, app_id)
        if not delete_volume(spec, environment):
            raise RuntimeError("download verified but dedicated Volume deletion failed")
    except BaseException as error:
        if app_id is None:
            after = list_apps(environment)
            app_id = resolve_app_id(set(), before, after, spec["modal_app_name"])
        if termination == "not_started":
            termination = stop_and_verify(app_id, environment)
        finish_attempt(
            ledger_path,
            reservation_id,
            app_id,
            (
                f"{type(error).__name__}; termination={termination}; "
                f"volume_retained={volume_created}; reconcile usage and recover bounded artifacts"
            ),
        )
        raise
    finish_attempt(
        ledger_path,
        reservation_id,
        app_id,
        "artifacts verified; exact app ID verified stopped; bounded Volume deleted; reconcile inclusive charge",
    )


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
        print(
            json.dumps(
                {"valid": True, "reservation": item, "entrypoint_present": entrypoint.is_file()},
                indent=2,
            )
        )
        return 0
    if not entrypoint.is_file():
        raise SystemExit(f"refusing cloud run: reviewed A07 entrypoint is missing: {entrypoint}")
    if not MODAL_CLI.is_file():
        raise SystemExit(f"refusing cloud run: isolated Modal CLI is missing: {MODAL_CLI}")
    output = Path(spec["output"]["local_path"])
    if output.exists():
        raise SystemExit(f"refusing cloud run: local output path already exists: {output}")
    environment = os.environ.copy()
    environment["HORIZON_MODAL_JOB_SPEC"] = str(spec_path)
    environment["HORIZON_DATA_ROOT"] = str(Path(spec["input"]["source_path"]).parents[1])
    environment["HORIZON_MODAL_RUN_ID"] = spec["job_id"]
    environment["HORIZON_MODAL_VOLUME"] = spec["modal_volume_name"]
    execute_job(args.ledger, args.reservation, spec, entrypoint, environment)
    print("Modal attempt finished; reconcile the inclusive provider charge before another reservation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
