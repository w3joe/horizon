"""Gate-owned A6 authorization for the bounded Singapore engineering policy.

Policy evidence is supplied by a trusted adapter, never by the decision AI.
The gate evaluates it locally against the exact A5 command; a caller-supplied
authorization is neither accepted nor necessary.
"""

from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from .validation import _contract_validator

from .policy_shadow import (
    A6PolicyShadow, PolicyBundle, PolicyBundleMetadata, PolicySourcePin,
    ShadowPolicyParameters, _bundle_status, _utc,
)


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _angle(value: float) -> float:
    return (math.degrees(value) + 180.0) % 360.0 - 180.0


def bind_command(evidence: Mapping[str, Any], governor_input: Mapping[str, Any],
                 command: Mapping[str, Any], parameters: ShadowPolicyParameters) -> dict[str, Any]:
    """Replace action claims with measurements of the command actually selected.

    Stopping distance must be conservatively supplied for this exact command by
    the evidence producer. Geometry, visibility and lookout remain source facts.
    """
    result = copy.deepcopy(dict(evidence))
    own = governor_input["snapshot"]["ownship"]
    change = _angle(float(command["heading_rad"]) - float(own["heading_rad"]))
    speed = float(command["speed_mps"])
    own_speed = math.hypot(*own["velocity_body_mps"][:2])
    reduction = own_speed - speed
    result.setdefault("safe_speed", {})["commanded_speed_mps"] = speed
    for contact in result.get("encounters", []):
        contact.update({
            "course_change_deg": change,
            "speed_reduction_mps": reduction,
            "action_detectable": (
                abs(change) >= parameters.substantial_course_change_deg
                or reduction >= parameters.substantial_speed_reduction_mps
            ),
            "course_and_speed_maintained": abs(change) < 1.0 and abs(reduction) < 0.1,
        })
    return result


class FilePolicyEvidence:
    """Read one atomically replaced, snapshot/command-bound local adapter record."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def __call__(self, governor_input: Mapping[str, Any], decision: Mapping[str, Any]) -> dict:
        # The operator configures this path at process startup. HTTP decision
        # bodies cannot choose a path or replace the trusted provider.
        with self.path.open("rb") as handle:
            payload = handle.read(1_000_001)
        if len(payload) > 1_000_000:
            raise ValueError("policy evidence exceeds size limit")
        record = json.loads(payload)
        if record.get("command_sha256") is None:
            # A live adapter can publish command-independent observations.
            # Bind those locally using its conservative braking envelope;
            # there is no need to predict A5's eventual filtered command.
            speed_evidence = record["evidence"]["safe_speed"]
            deceleration = speed_evidence["minimum_deceleration_mps2"]
            response_time = speed_evidence["maximum_response_time_s"]
            if (isinstance(deceleration, bool) or isinstance(response_time, bool)
                    or not math.isfinite(deceleration) or not math.isfinite(response_time)
                    or deceleration <= 0 or response_time < 0):
                raise ValueError("invalid conservative braking envelope")
            speed = float(decision["issued_command"]["speed_mps"])
            own_speed = math.hypot(*governor_input["snapshot"]["ownship"]["velocity_body_mps"][:2])
            braking_speed = max(speed, own_speed)
            speed_evidence["stopping_distance_m"] = braking_speed * response_time + braking_speed**2 / (2 * deceleration)
            record["command_sha256"] = content_hash(decision["issued_command"])
        return record


class A6PolicyEnforcer:
    evaluator_version = "a6-singapore-gate-enforcement-v1"

    def __init__(self, bundle: PolicyBundle | None = None,
                 evidence_provider: Callable | None = None, *,
                 allow_synthetic: bool = False, maximum_compute_ns: int = 10_000_000,
                 utc_now: Callable[[], datetime] | None = None):
        self.bundle = bundle
        self.evidence_provider = evidence_provider
        self.allow_synthetic = allow_synthetic
        self.maximum_compute_ns = maximum_compute_ns
        self.utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self.validator = _contract_validator("PolicyEvidence")

    @classmethod
    def from_files(cls, config_path: str | Path, evidence_path: str | Path):
        """Pin source artifact bytes and immutable bundle at process startup.

        Operational deployments require recorded source provenance. Merely
        copying a hash into a bundle is insufficient: each local source is read
        and checked. Applicability/interpretation is still operator configured.
        """
        path = Path(config_path).resolve()
        config = json.loads(path.read_text())
        if config.get("provenance") != "recorded":
            raise ValueError("operational policy sources must be recorded")
        metadata = dict(config["bundle"]["metadata"])
        metadata["source_pins"] = tuple(PolicySourcePin(**pin) for pin in metadata["source_pins"])
        bundle = PolicyBundle(PolicyBundleMetadata(**metadata),
                              ShadowPolicyParameters(**config["bundle"]["parameters"]))
        for pin in bundle.metadata.source_pins:
            artifact = path.parent / config["source_artifacts"][pin.source_id]
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != pin.content_sha256:
                raise ValueError(f"policy source hash mismatch: {pin.source_id}")
        okay, reasons = _bundle_status(bundle, datetime.now(timezone.utc).isoformat())
        if not okay:
            raise ValueError(",".join(reasons))
        return cls(bundle, FilePolicyEvidence(evidence_path))

    def evaluate(self, governor_input: Mapping[str, Any], decision: Mapping[str, Any], *,
                 now_ns: int) -> dict[str, Any]:
        started = time.perf_counter_ns()
        reasons: list[str] = []
        shadow = None
        evidence_hash = None
        bundle_hash = content_hash(asdict(self.bundle)) if self.bundle else None
        expiry = min(int(decision["expires_monotonic_ns"]),
                     int(governor_input["decision_deadline_monotonic_ns"]))
        try:
            if self.bundle is None or self.evidence_provider is None:
                reasons.append("A6_POLICY_OR_EVIDENCE_UNAVAILABLE")
            else:
                record = copy.deepcopy(self.evidence_provider(copy.deepcopy(governor_input), copy.deepcopy(decision)))
                if self.validator is None:
                    raise ValueError("policy schema unavailable")
                self.validator.validate(record)
                if record.get("contract_type") != "PolicyEvidence":
                    raise ValueError("wrong policy evidence contract")
                evidence_hash = content_hash(record)
                if record["provenance"] != "recorded" and not (
                    self.allow_synthetic and record["provenance"] == "synthetic"
                ):
                    reasons.append("A6_EVIDENCE_PROVENANCE_UNSUPPORTED")
                for field in ("run_id", "branch_id", "tick_index"):
                    if record[field] != governor_input[field]:
                        reasons.append("A6_EVIDENCE_IDENTITY_MISMATCH")
                if record["snapshot_sha256"] != content_hash(governor_input["snapshot"]):
                    reasons.append("A6_SNAPSHOT_HASH_MISMATCH")
                if record["command_sha256"] != content_hash(decision["issued_command"]):
                    reasons.append("A6_COMMAND_HASH_MISMATCH")
                if not (record["observed_monotonic_ns"] <= now_ns < record["expires_monotonic_ns"]):
                    reasons.append("A6_EVIDENCE_STALE_OR_FUTURE")
                expiry = min(expiry, record["expires_monotonic_ns"])
                now_utc = self.utc_now()
                # Carry bundle expiry into the monotonic dispatch check too.
                remaining = (_utc(self.bundle.metadata.valid_until_utc) - now_utc).total_seconds()
                expiry = min(expiry, now_ns + max(0, int(remaining * 1e9)))
                evidence = bind_command(record["evidence"], governor_input,
                                        decision["issued_command"], self.bundle.parameters)
                # Unsupported vessel classes and channel/TSS navigation must
                # not disappear as 'not applicable' in a bounded rule slice.
                context = record["operational_context"]
                if context.get("in_narrow_channel") is not False or context.get("in_traffic_separation_scheme") is not False:
                    reasons.append("A6_CHANNEL_OR_TSS_UNSUPPORTED_OR_UNKNOWN")
                if any(item.get("in_sight") is not True or item.get("power_driven") is not True
                       for item in evidence.get("encounters", [])):
                    reasons.append("A6_CONTACT_OUTSIDE_SUPPORTED_SLICE")
                if not reasons:
                    shadow = A6PolicyShadow().evaluate(
                        governor_input=governor_input, a5_decision=decision,
                        operational_context=context, evidence=evidence,
                        bundle=self.bundle, evaluated_at_utc=now_utc.isoformat(),
                    )
                    if shadow["shadow_support"] != "supported":
                        reasons.extend(shadow["reason_codes"])
        except Exception as exc:
            # Adapter I/O, schema failures and evaluator errors fail closed.
            reasons.extend(("A6_EVALUATION_FAILED", type(exc).__name__))
        elapsed = time.perf_counter_ns() - started
        if elapsed > self.maximum_compute_ns:
            reasons.append("A6_COMPUTE_BUDGET_EXCEEDED")
        if now_ns >= expiry:
            reasons.append("A6_AUTHORIZATION_EXPIRED")
        record = {
            "contract_type": "A6PolicyDecision", "schema_version": "0.1.0",
            "evaluator_version": self.evaluator_version,
            "run_id": governor_input["run_id"], "branch_id": governor_input["branch_id"],
            "tick_index": governor_input["tick_index"], "a5_decision_id": decision["decision_id"],
            "input_sha256": content_hash(governor_input), "decision_sha256": content_hash(decision),
            "bundle_sha256": bundle_hash, "evidence_sha256": evidence_hash,
            "authorization": "withhold" if reasons else "authorize",
            "expires_monotonic_ns": expiry, "compute_time_ns": elapsed,
            "reason_codes": list(dict.fromkeys(reasons)) if reasons else ["A6_POLICY_AUTHORIZED"],
            "findings": shadow["findings"] if shadow else [],
        }
        record["assessment_id"] = "a6:" + content_hash(record)
        return record
