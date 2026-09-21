"""Transparent nominal and faulty decision-AI test doubles."""

from __future__ import annotations

import math
import time
from typing import Any


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


class FixturePolicy:
    def __init__(self, mode: str = "nominal", *, model_version: str | None = None):
        if mode not in {"nominal", "unsafe_straight", "expired", "stale_lineage"}:
            raise ValueError(f"unsupported fixture policy: {mode}")
        self.mode = mode
        self.model_version = model_version or f"decision-ai-fixture-{mode}-v1"
        self.sequence = 0

    def propose(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        start_ns = time.monotonic_ns()
        ownship = snapshot["ownship"]
        heading = float(ownship["heading_rad"])
        speed = 4.0
        if self.mode == "nominal":
            nearest = self._nearest_contact(snapshot)
            if nearest and nearest[0] < 80.0:
                # Deliberately simple planner behavior. The independent RTA is
                # still responsible for validating the resulting command.
                heading = _wrap(heading + math.radians(25.0))
                speed = 2.0
        elif self.mode == "unsafe_straight":
            speed = 6.0

        sim_time = float(snapshot["simulation_time_s"])
        issued_ns = round(sim_time * 1e9)
        expiry_s = sim_time + 0.35
        expiry_ns = issued_ns + 350_000_000
        origin_snapshot_id = str(snapshot["snapshot_id"])
        if self.mode == "expired":
            expiry_s = max(0.0, sim_time - 0.01)
            expiry_ns = max(0, issued_ns - 10_000_000)
        elif self.mode == "stale_lineage":
            origin_snapshot_id = "stale-or-unknown-snapshot"

        command_id = f"{snapshot['run_id']}:{snapshot['branch_id']}:ai:{self.sequence}"
        trace_id = f"{command_id}:trace"
        proposal = {
            "contract_type": "ProposedCommand",
            "schema_version": "0.1.0",
            "run_id": snapshot["run_id"],
            "branch_id": snapshot["branch_id"],
            "command_id": command_id,
            "source_id": "decision-ai-fixture",
            "authority": "decision_ai",
            "sequence": self.sequence,
            "origin_snapshot_id": origin_snapshot_id,
            "issued_monotonic_ns": issued_ns,
            "expires_monotonic_ns": expiry_ns,
            "issued_simulation_time_s": sim_time,
            "expires_simulation_time_s": expiry_s,
            "command": {"heading_rad": heading, "speed_mps": speed},
            "inference_trace_id": trace_id,
        }
        completed_ns = time.monotonic_ns()
        trace = {
            "contract_type": "AIInferenceTrace",
            "schema_version": "0.1.0",
            "trace_id": trace_id,
            "run_id": snapshot["run_id"],
            "branch_id": snapshot["branch_id"],
            "source_id": "decision-ai-fixture",
            "model_version": self.model_version,
            "consumed_input_ids": [str(snapshot["snapshot_id"])],
            "started_monotonic_ns": start_ns,
            "completed_monotonic_ns": completed_ns,
            "candidate_scores": {"route_progress": 1.0, "contact_avoidance": 0.5 if self.mode == "nominal" else 0.0},
            "status": "ok",
        }
        self.sequence += 1
        return proposal, trace

    @staticmethod
    def _nearest_contact(snapshot: dict[str, Any]) -> tuple[float, dict[str, Any]] | None:
        own = snapshot["ownship"]["position_ne_m"]
        contacts = snapshot.get("traffic", [])
        if not contacts:
            return None
        distances = [
            (math.hypot(item["position_ne_m"][0] - own[0], item["position_ne_m"][1] - own[1]), item)
            for item in contacts
        ]
        return min(distances, key=lambda pair: pair[0])
