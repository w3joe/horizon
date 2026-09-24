"""A6 observation adapter for paired A5 closed-loop development runs.

The adapter derives bounded engineering evidence from the public governor input
and completed A5 decision.  It never reads simulator truth and never writes to
the gate or plant.  Its synthetic policy source pins and vessel-profile facts
make the resulting artifact development evidence, not legal validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timezone
import math
import time
from typing import Any

from horizon_assurance.policy_shadow import (
    A6PolicyShadow,
    PolicyBundle,
    PolicyBundleMetadata,
    PolicySourcePin,
    ShadowPolicyParameters,
    policy_content_sha256,
)


def _angle_deg(value: float) -> float:
    return (math.degrees(value) + 180.0) % 360.0 - 180.0


def _heading_between(delta_north: float, delta_east: float) -> float:
    return math.atan2(delta_east, delta_north)


def _velocity_ne(vessel: Mapping[str, Any]) -> tuple[float, float]:
    velocity = vessel.get("velocity_ne_mps")
    if isinstance(velocity, list) and len(velocity) == 2:
        return float(velocity[0]), float(velocity[1])
    body = vessel.get("velocity_body_mps")
    speed = float(body[0]) if isinstance(body, list) and body else 0.0
    heading = float(vessel.get("heading_rad", 0.0))
    return speed * math.cos(heading), speed * math.sin(heading)


def _configuration(config: Mapping[str, Any]) -> tuple[PolicyBundle, dict[str, Any], str]:
    parameters = ShadowPolicyParameters(**config["parameters"])
    pins = tuple(PolicySourcePin(**item) for item in config["source_pins"])
    bundle_id = str(config["bundle_id"])
    version = str(config["version"])
    digest = policy_content_sha256(
        bundle_id=bundle_id,
        version=version,
        parameters=parameters,
        source_pins=pins,
    )
    bundle = PolicyBundle(
        metadata=PolicyBundleMetadata(
            bundle_id=bundle_id,
            version=version,
            content_sha256=digest,
            valid_from_utc=str(config["valid_from_utc"]),
            valid_until_utc=str(config["valid_until_utc"]),
            source_pins=pins,
        ),
        parameters=parameters,
    )
    return bundle, dict(config["operational_context"]), str(config["evaluated_at_utc"])


def _evidence(
    governor_input: Mapping[str, Any],
    a5_decision: Mapping[str, Any],
    parameters: ShadowPolicyParameters,
) -> dict[str, Any]:
    snapshot = governor_input["snapshot"]
    ownship = snapshot["ownship"]
    issued = a5_decision.get("issued_command")
    command = issued if isinstance(issued, Mapping) else governor_input["proposal"]["command"]
    commanded_speed = max(0.0, float(command["speed_mps"]))
    commanded_heading = float(command["heading_rad"])
    own_position = ownship["position_ne_m"]
    own_heading = float(ownship["heading_rad"])
    own_velocity = _velocity_ne(ownship)
    own_speed = math.hypot(*own_velocity)
    contacts = snapshot.get("contacts", [])
    ranges = [
        math.hypot(
            float(item["position_ne_m"][0]) - float(own_position[0]),
            float(item["position_ne_m"][1]) - float(own_position[1]),
        )
        for item in contacts
    ]
    clear_distance = min(ranges, default=10_000.0)
    assumed_deceleration_mps2 = 0.5
    stopping_distance = commanded_speed**2 / (2.0 * assumed_deceleration_mps2)
    course_change = _angle_deg(commanded_heading - own_heading)
    speed_reduction = max(0.0, own_speed - commanded_speed)
    action_detectable = (
        abs(course_change) >= parameters.substantial_course_change_deg
        or speed_reduction >= parameters.substantial_speed_reduction_mps
    )

    encounters = []
    for contact in contacts:
        contact_position = contact["position_ne_m"]
        delta_north = float(contact_position[0]) - float(own_position[0])
        delta_east = float(contact_position[1]) - float(own_position[1])
        contact_heading = float(contact.get("heading_rad", 0.0))
        contact_velocity = _velocity_ne(contact)
        relative_velocity = (
            contact_velocity[0] - own_velocity[0],
            contact_velocity[1] - own_velocity[1],
        )
        velocity_sq = relative_velocity[0] ** 2 + relative_velocity[1] ** 2
        tcpa = (
            max(
                0.0,
                -(
                    delta_north * relative_velocity[0]
                    + delta_east * relative_velocity[1]
                )
                / velocity_sq,
            )
            if velocity_sq > 1.0e-9
            else 1.0e9
        )
        cpa_north = delta_north + relative_velocity[0] * tcpa
        cpa_east = delta_east + relative_velocity[1] * tcpa
        dcpa = math.hypot(cpa_north, cpa_east)
        collision_risk = tcpa <= 120.0 and dcpa <= 100.0
        bearing_to_contact = _heading_between(delta_north, delta_east)
        bearing_to_ownship = _heading_between(-delta_north, -delta_east)
        encounters.append(
            {
                "contact_id": str(contact["contact_id"]),
                "in_sight": True,
                "power_driven": True,
                "relative_bearing_deg": _angle_deg(bearing_to_contact - own_heading),
                "ownship_bearing_from_contact_deg": _angle_deg(
                    bearing_to_ownship - contact_heading
                ),
                "course_difference_deg": _angle_deg(own_heading - contact_heading),
                "risk_doubt": collision_risk,
                "treated_as_collision_risk": collision_risk,
                "action_lead_time_s": tcpa if collision_risk else 1.0e9,
                "course_change_deg": course_change,
                "speed_reduction_mps": speed_reduction,
                "action_detectable": action_detectable,
                "course_and_speed_maintained": (
                    abs(course_change) < 1.0 and speed_reduction < 0.1
                ),
            }
        )
    return {
        "lookout": {
            # These are declared synthetic study capabilities, not inferred
            # from simulator truth or claimed for an operational vessel.
            "visual_watch_available": True,
            "auditory_watch_available": True,
            "radar_watch_available": True,
            "remote_supervisor_available": True,
            "evidence_fresh": True,
        },
        "safe_speed": {
            "commanded_speed_mps": commanded_speed,
            "stopping_distance_m": stopping_distance,
            "clear_distance_m": clear_distance,
            "traffic_assessment_available": True,
        },
        "encounters": encounters,
    }


class A6DevelopmentObserver:
    """Evaluate and summarize A6 without affecting simulated time or control."""

    def __init__(self, config: Mapping[str, Any]):
        self.bundle, self.context, self.evaluated_at_utc = _configuration(config)
        self.evaluator = A6PolicyShadow()
        self.assessments: list[dict[str, Any]] = []
        self.wall_time_ns: list[int] = []

    def evaluate(
        self, governor_input: Mapping[str, Any], a5_decision: Mapping[str, Any]
    ) -> dict[str, Any]:
        evidence = _evidence(governor_input, a5_decision, self.bundle.parameters)
        started = time.perf_counter_ns()
        assessment = self.evaluator.evaluate(
            governor_input=governor_input,
            a5_decision=a5_decision,
            operational_context=self.context,
            evidence=evidence,
            bundle=self.bundle,
            evaluated_at_utc=self.evaluated_at_utc,
        )
        elapsed = time.perf_counter_ns() - started
        self.wall_time_ns.append(elapsed)
        self.assessments.append(assessment)
        return assessment

    def result(self) -> dict[str, Any]:
        support_counts: dict[str, int] = {}
        reason_counts: dict[str, int] = {}
        finding_counts: dict[str, dict[str, int]] = {}
        for assessment in self.assessments:
            support = str(assessment["shadow_support"])
            support_counts[support] = support_counts.get(support, 0) + 1
            for reason in assessment["reason_codes"]:
                reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
            for finding in assessment["findings"]:
                rule = str(finding["rule_id"])
                state = str(finding["finding"])
                per_rule = finding_counts.setdefault(rule, {})
                per_rule[state] = per_rule.get(state, 0) + 1
        ordered = sorted(self.wall_time_ns)

        def percentile(fraction: float) -> int | None:
            if not ordered:
                return None
            index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
            return ordered[index]

        return {
            "record_type": "A6DevelopmentShadowSummary",
            "schema_version": "local-a6-development-shadow-v1",
            "observation_only": True,
            "control_authority": "none",
            "legal_compliance_determination": False,
            "assessment_count": len(self.assessments),
            "support_counts": dict(sorted(support_counts.items())),
            "reason_counts": dict(sorted(reason_counts.items())),
            "finding_counts": {
                rule: dict(sorted(counts.items()))
                for rule, counts in sorted(finding_counts.items())
            },
            "wall_time_ns": {
                "count": len(ordered),
                "samples": ordered,
                "p50": percentile(0.50),
                "p95": percentile(0.95),
                "p99": percentile(0.99),
                "max": max(ordered) if ordered else None,
            },
            "policy_bundle": {
                "bundle_id": self.bundle.metadata.bundle_id,
                "version": self.bundle.metadata.version,
                "content_sha256": self.bundle.metadata.content_sha256,
                "source_pins": [asdict(item) for item in self.bundle.metadata.source_pins],
            },
            "operational_context": dict(self.context),
            "limitations": [
                "DEVELOPMENT_SPLIT_ONLY",
                "SYNTHETIC_POLICY_SOURCE_PINS",
                "SYNTHETIC_LOOKOUT_CAPABILITIES",
                "PUBLIC_STATE_ENGINEERING_EVIDENCE_ADAPTER",
                "NOT_LEGAL_VALIDATION",
            ],
            "generated_at_utc": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
        }
