"""Transparent nominal and faulty decision-AI test doubles."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


class FixturePolicy:
    def __init__(
        self,
        mode: str = "nominal",
        *,
        camera_reliance: str = "radar_only",
        model_version: str | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ):
        if mode not in {
            "nominal",
            "unsafe_straight",
            "expired",
            "stale_lineage",
            "malformed",
        }:
            raise ValueError(f"unsupported fixture policy: {mode}")
        if camera_reliance not in {"radar_only", "recorded_camera_supporting"}:
            raise ValueError(f"unsupported camera reliance: {camera_reliance}")
        self.mode = mode
        self.camera_reliance = camera_reliance
        default_model = (
            f"decision-ai-fixture-{mode}-v1"
            if camera_reliance == "radar_only"
            else f"decision-ai-fixture-{mode}-{camera_reliance}-v1"
        )
        self.model_version = model_version or default_model
        self._monotonic_ns = monotonic_ns
        self.sequence = 0

    def propose(
        self,
        snapshot: dict[str, Any],
        perception_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        start_ns = self._monotonic_ns()
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

        # This fixture can model a deployment whose primary AI is configured
        # to rely on camera support. The context can only constrain it. It
        # cannot grant geometric authority or relax radar requirements.
        camera_support_usable = bool(
            perception_context is not None
            and perception_context["health_status"] == "healthy"
            and perception_context["camera_free_space_usable"] is True
            and perception_context["metric_contacts_usable"] is True
            and perception_context["calibrated_risk_band"] != "unknown"
        )
        if self.camera_reliance == "recorded_camera_supporting" and not camera_support_usable:
            speed = min(speed, 1.0)

        h5_warning = self._simulation_h5_warning(snapshot, perception_context, start_ns)
        if h5_warning:
            speed = min(speed, 1.0)

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
        if self.mode == "malformed":
            # Deliberately violates ProposedCommand. The standalone service
            # still returns JSON so boundary validation is exercised.
            proposal["command"]["speed_mps"] = "six"
        completed_ns = self._monotonic_ns()
        consumed_input_ids = [str(snapshot["snapshot_id"])]
        if perception_context is not None:
            consumed_input_ids.append(str(perception_context["health_id"]))
        trace = {
            "contract_type": "AIInferenceTrace",
            "schema_version": "0.1.0",
            "trace_id": trace_id,
            "run_id": snapshot["run_id"],
            "branch_id": snapshot["branch_id"],
            "source_id": "decision-ai-fixture",
            "model_version": self.model_version,
            "consumed_input_ids": consumed_input_ids,
            "started_monotonic_ns": start_ns,
            "completed_monotonic_ns": completed_ns,
            "candidate_scores": {
                "route_progress": 1.0,
                "contact_avoidance": 0.5 if self.mode == "nominal" else 0.0,
                "camera_support_usable": 1.0 if camera_support_usable else 0.0,
                "h5_simulation_warning": 1.0 if h5_warning else 0.0,
            },
            "status": "ok",
        }
        self.sequence += 1
        return proposal, trace

    @staticmethod
    def _simulation_h5_warning(snapshot: dict, context: dict | None, now_ns: int) -> bool:
        """A fresh H5 demo warning can only lower a simulated speed proposal."""
        if (
            snapshot.get("contract_type") != "SimulationSnapshot"
            or snapshot.get("display_only") is not True
            or context is None or context.get("method_id") != "H5"
            or context.get("valid_until_monotonic_ns", 0) <= now_ns
        ):
            return False
        warning = context.get("simulation_h5_warning")
        if warning is None:
            return False
        if not isinstance(warning, dict) or warning.get("mode") != "simulation_warning":
            raise ValueError("invalid H5 simulation warning")
        threshold = warning.get("threshold")
        if (
            isinstance(threshold, bool) or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold) or threshold <= 0
        ):
            raise ValueError("invalid H5 simulation threshold")
        score = warning.get("score")
        if warning.get("status") == "unknown" and score is None:
            return False
        if (
            isinstance(score, bool) or not isinstance(score, (int, float))
            or not math.isfinite(score) or score < 0
        ):
            raise ValueError("invalid H5 simulation score")
        active = score >= threshold
        if warning.get("status") != ("warning" if active else "below_threshold"):
            raise ValueError("H5 simulation status does not match score")
        return active

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
