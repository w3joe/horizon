#!/usr/bin/env python3
"""Maintain the Horizon-only external compute ledger with an exclusive lock."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "infra/compute-ledger.template.json"
ACTIVE_STATUSES = {"authorized", "running", "awaiting_reconciliation"}


def default_ledger_path() -> Path:
    configured = os.environ.get("HORIZON_COMPUTE_LEDGER")
    if configured:
        return Path(configured).expanduser().resolve()
    common = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"], cwd=ROOT, text=True
    ).strip()
    repository = (ROOT / common).resolve().parent
    return repository.parent / "horizon-runs/compute/ledger.json"


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            return
        payload = json.loads(TEMPLATE.read_text())
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(path)


def finite_nonnegative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return numeric


def validate(ledger: dict[str, Any]) -> None:
    budget = ledger["budget"]
    total_cap = finite_nonnegative(budget["total_cap"], "total cap")
    working_cap = finite_nonnegative(budget["working_cap"], "working cap")
    reserve = finite_nonnegative(budget["protected_reserve"], "protected reserve")
    finite_nonnegative(budget["reconciled_spend"], "reconciled spend")
    if abs(total_cap - working_cap - reserve) > 1e-9:
        raise ValueError("total cap must equal working cap plus protected reserve")
    for name, value in ledger["rates"].items():
        if name != "effective_date":
            finite_nonnegative(value, f"rate {name}")
    for item in ledger["reservations"]:
        finite_nonnegative(item["upper_bound_usd"], f"reservation {item['reservation_id']} upper bound")
        if item["actual_usd"] is not None:
            finite_nonnegative(item["actual_usd"], f"reservation {item['reservation_id']} actual cost")
        attempts = item["attempts_started"]
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise ValueError("attempt count must be a non-negative integer")
    reservation_ids = [item["reservation_id"] for item in ledger["reservations"]]
    if len(reservation_ids) != len(set(reservation_ids)):
        raise ValueError("duplicate reservation ID")


def mutate(path: Path, operation: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    initialize(path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = json.loads(path.read_text())
        validate(ledger)
        operation(ledger)
        validate(ledger)
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
            json.dump(ledger, handle, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(path)
        return ledger


def reservation(ledger: dict[str, Any], reservation_id: str) -> dict[str, Any]:
    for item in ledger["reservations"]:
        if item["reservation_id"] == reservation_id:
            return item
    raise ValueError(f"unknown reservation {reservation_id!r}")


def begin(path: Path, reservation_id: str) -> dict[str, Any]:
    def operation(ledger: dict[str, Any]) -> None:
        item = reservation(ledger, reservation_id)
        if item["status"] != "authorized" or item["attempts_started"] != 0:
            raise ValueError("reservation is not eligible for its one allowed attempt")
        budget = ledger["budget"]
        active = sum(
            entry["upper_bound_usd"]
            for entry in ledger["reservations"]
            if entry["status"] in ACTIVE_STATUSES
        )
        if budget["reconciled_spend"] + active > budget["working_cap"] + 1e-9:
            raise ValueError("budget is overcommitted; no compute attempt may begin")
        item["status"] = "running"
        item["attempts_started"] = 1

    return mutate(path, operation)


def reserve(
    path: Path,
    reservation_id: str,
    job_spec: str,
    upper_bound_usd: float,
    authorization: str,
) -> dict[str, Any]:
    upper_bound = finite_nonnegative(upper_bound_usd, "reservation upper bound")
    if not reservation_id or not job_spec or not authorization:
        raise ValueError("reservation ID, job spec, and authorization are required")

    def operation(ledger: dict[str, Any]) -> None:
        if any(item["reservation_id"] == reservation_id for item in ledger["reservations"]):
            raise ValueError(f"duplicate reservation ID {reservation_id!r}")
        budget = ledger["budget"]
        active = sum(
            entry["upper_bound_usd"]
            for entry in ledger["reservations"]
            if entry["status"] in ACTIVE_STATUSES
        )
        if budget["reconciled_spend"] + active + upper_bound > budget["working_cap"] + 1e-9:
            raise ValueError("reservation would exceed the working cap")
        ledger["reservations"].append(
            {
                "reservation_id": reservation_id,
                "job_spec": job_spec,
                "status": "authorized",
                "upper_bound_usd": upper_bound,
                "actual_usd": None,
                "authorized_utc": None,
                "authorization": authorization,
                "attempts_started": 0,
                "provider_job_id": None,
                "reconciliation_note": None,
            }
        )

    return mutate(path, operation)


def finish_attempt(path: Path, reservation_id: str, provider_job_id: str | None, note: str) -> dict[str, Any]:
    def operation(ledger: dict[str, Any]) -> None:
        item = reservation(ledger, reservation_id)
        if item["status"] != "running":
            raise ValueError("reservation is not running")
        item["status"] = "awaiting_reconciliation"
        item["provider_job_id"] = provider_job_id
        item["reconciliation_note"] = note

    return mutate(path, operation)


def reconcile(path: Path, reservation_id: str, actual_usd: float, note: str) -> dict[str, Any]:
    def operation(ledger: dict[str, Any]) -> None:
        item = reservation(ledger, reservation_id)
        if item["status"] not in {"running", "awaiting_reconciliation"}:
            raise ValueError("only a started attempt can be reconciled")
        actual = finite_nonnegative(actual_usd, "actual cost")
        item["status"] = "reconciled_overrun" if actual > item["upper_bound_usd"] else "reconciled"
        item["actual_usd"] = actual
        item["reconciliation_note"] = note
        ledger["budget"]["reconciled_spend"] = sum(
            entry["actual_usd"] or 0.0 for entry in ledger["reservations"]
        )

    return mutate(path, operation)


def summary(ledger: dict[str, Any]) -> dict[str, Any]:
    budget = ledger["budget"]
    reserved = sum(
        item["upper_bound_usd"]
        for item in ledger["reservations"]
        if item["status"] in ACTIVE_STATUSES
    )
    committed = budget["reconciled_spend"] + reserved
    return {
        "ledger": str(default_ledger_path()),
        "total_cap_usd": budget["total_cap"],
        "protected_reserve_usd": budget["protected_reserve"],
        "working_cap_usd": budget["working_cap"],
        "reconciled_spend_usd": budget["reconciled_spend"],
        "active_reservations_usd": round(reserved, 6),
        "unreserved_working_budget_usd": round(
            max(0.0, budget["working_cap"] - committed), 6
        ),
        "budget_state": "overrun" if committed > budget["working_cap"] else "within_cap",
        "reservations": ledger["reservations"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=default_ledger_path())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("status")
    reserve_parser = commands.add_parser("reserve")
    reserve_parser.add_argument("--reservation", required=True)
    reserve_parser.add_argument("--job-spec", required=True)
    reserve_parser.add_argument("--upper-bound-usd", required=True, type=float)
    reserve_parser.add_argument("--authorization", required=True)
    reconcile_parser = commands.add_parser("reconcile")
    reconcile_parser.add_argument("--reservation", required=True)
    reconcile_parser.add_argument("--actual-usd", required=True, type=float)
    reconcile_parser.add_argument("--note", required=True)
    args = parser.parse_args()
    path = args.ledger.expanduser().resolve()
    initialize(path)
    if args.command == "reserve":
        ledger = reserve(
            path,
            args.reservation,
            args.job_spec,
            args.upper_bound_usd,
            args.authorization,
        )
    elif args.command == "reconcile":
        ledger = reconcile(path, args.reservation, args.actual_usd, args.note)
    else:
        ledger = json.loads(path.read_text())
        validate(ledger)
    output = summary(ledger)
    output["ledger"] = str(path)
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
