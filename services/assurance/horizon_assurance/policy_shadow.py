"""Deterministic, observation-only A6 maritime policy shadow.

This module is deliberately outside the ``Candidate`` hierarchy.  It consumes
an A5 result but cannot create an AssuranceDecision, submit to the gate, or
write an actuator command.  Its rule findings are engineering proxies for a
small clear-visibility policy slice; they are not a determination of COLREG
or other legal compliance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Literal, Mapping


Applicability = Literal["applicable", "not_applicable", "unknown"]
Finding = Literal["satisfied", "not_satisfied", "unknown", "not_evaluated"]

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_A5_PERMISSIVE_ACTIONS = frozenset(("pass", "modify"))
_SUPPORTED_VISIBILITY = "clear"
_EXCLUDED_DOMAIN_KEYS = frozenset(
    ("weapons", "weapon", "targeting", "classified_doctrine", "rules_of_engagement", "roe")
)


@dataclass(frozen=True)
class PolicySourcePin:
    """Identifier and caller-supplied digest for one source artifact.

    The evaluator never supplies source text or placeholder digests.  The
    source loader is responsible for hashing the exact artifact bytes and
    constructing this value.
    """

    source_id: str
    revision: str
    role: Literal["runtime_authority", "design_assurance_reference"]
    content_sha256: str


@dataclass(frozen=True)
class ShadowPolicyParameters:
    """Versioned engineering thresholds, not legal thresholds."""

    maximum_clear_visibility_speed_mps: float
    stopping_distance_reserve_m: float
    minimum_early_action_lead_s: float
    substantial_course_change_deg: float
    substantial_speed_reduction_mps: float
    head_on_bearing_tolerance_deg: float
    reciprocal_course_tolerance_deg: float
    classification_ambiguity_deg: float
    overtaking_abaft_beam_deg: float


@dataclass(frozen=True)
class PolicyBundleMetadata:
    bundle_id: str
    version: str
    content_sha256: str
    valid_from_utc: str
    valid_until_utc: str
    source_pins: tuple[PolicySourcePin, ...]


@dataclass(frozen=True)
class PolicyBundle:
    metadata: PolicyBundleMetadata
    parameters: ShadowPolicyParameters

    def canonical_content(self) -> bytes:
        """Return the exact bytes covered by ``metadata.content_sha256``."""

        payload = {
            "bundle_id": self.metadata.bundle_id,
            "parameters": asdict(self.parameters),
            "source_pins": [asdict(item) for item in self.metadata.source_pins],
            "version": self.metadata.version,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def policy_content_sha256(
    *,
    bundle_id: str,
    version: str,
    parameters: ShadowPolicyParameters,
    source_pins: tuple[PolicySourcePin, ...],
) -> str:
    """Hash caller-selected policy content without inventing source hashes."""

    payload = {
        "bundle_id": bundle_id,
        "parameters": asdict(parameters),
        "source_pins": [asdict(item) for item in source_pins],
        "version": version,
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _tri_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _contains_excluded_domain(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _EXCLUDED_DOMAIN_KEYS
            or _contains_excluded_domain(nested)
            for key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_excluded_domain(item) for item in value)
    return False


def _finding(
    rule_id: str,
    applicability: Applicability,
    finding: Finding,
    reasons: list[str] | tuple[str, ...],
    *,
    contact_id: str | None = None,
    duty: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "rule_id": rule_id,
        "applicability": applicability,
        "finding": finding,
        "reason_codes": list(dict.fromkeys(reasons)),
    }
    if contact_id is not None:
        result["contact_id"] = contact_id
    if duty is not None:
        result["shadow_duty"] = duty
    return result


def _scope(context: Mapping[str, Any]) -> tuple[Applicability, list[str]]:
    visibility = context.get("visibility")
    if visibility == "restricted":
        return "unknown", ["RESTRICTED_VISIBILITY_UNSUPPORTED"]
    if visibility not in {_SUPPORTED_VISIBILITY, None}:
        return "unknown", ["VISIBILITY_CLASSIFICATION_UNKNOWN"]

    exact = {
        "jurisdiction": "singapore",
        "service_type": "government_non_commercial",
    }
    booleans = {
        "power_driven": True,
        "carries_passengers": False,
        "towing": False,
        "hazardous_cargo": False,
        "remote_supervision": True,
        "daylight": True,
    }
    unknown: list[str] = []
    outside: list[str] = []
    for name, expected in exact.items():
        actual = context.get(name)
        if actual is None:
            unknown.append(f"SCOPE_FACT_MISSING:{name}")
        elif actual != expected:
            outside.append(f"OUTSIDE_PROFILE:{name}")
    for name, expected in booleans.items():
        actual = _tri_bool(context.get(name))
        if actual is None:
            unknown.append(f"SCOPE_FACT_MISSING:{name}")
        elif actual is not expected:
            outside.append(f"OUTSIDE_PROFILE:{name}")

    length = _finite_number(context.get("length_m"))
    tonnage = _finite_number(context.get("gross_tonnage"))
    if length is None:
        unknown.append("SCOPE_FACT_MISSING:length_m")
    elif not (0.0 < length < 50.0):
        outside.append("OUTSIDE_PROFILE:length_m")
    if tonnage is None:
        unknown.append("SCOPE_FACT_MISSING:gross_tonnage")
    elif not (0.0 <= tonnage < 300.0):
        outside.append("OUTSIDE_PROFILE:gross_tonnage")
    if visibility is None:
        unknown.append("SCOPE_FACT_MISSING:visibility")

    if outside:
        return "not_applicable", outside
    if unknown:
        return "unknown", unknown
    return "applicable", ["BOUNDED_SINGAPORE_GOVERNMENT_USV_PROFILE"]


def _bundle_status(
    bundle: PolicyBundle | None, evaluated_at_utc: str
) -> tuple[bool, list[str]]:
    if bundle is None:
        return False, ["POLICY_BUNDLE_MISSING"]
    reasons: list[str] = []
    metadata = bundle.metadata
    if not _SHA256.fullmatch(metadata.content_sha256):
        reasons.append("POLICY_BUNDLE_HASH_INVALID")
    try:
        actual = hashlib.sha256(bundle.canonical_content()).hexdigest()
    except (TypeError, ValueError):
        actual = None
        reasons.append("POLICY_BUNDLE_CONTENT_INVALID")
    if actual is not None and actual != metadata.content_sha256:
        reasons.append("POLICY_BUNDLE_HASH_MISMATCH")
    if not metadata.source_pins:
        reasons.append("POLICY_SOURCE_PINS_MISSING")
    for source in metadata.source_pins:
        if not source.source_id or not source.revision or not _SHA256.fullmatch(
            source.content_sha256
        ):
            reasons.append("POLICY_SOURCE_PIN_INVALID")
        if source.role not in {"runtime_authority", "design_assurance_reference"}:
            reasons.append("POLICY_SOURCE_ROLE_INVALID")
    if metadata.source_pins and not any(
        source.role == "runtime_authority" for source in metadata.source_pins
    ):
        reasons.append("POLICY_RUNTIME_AUTHORITY_PIN_MISSING")
    try:
        now = _utc(evaluated_at_utc)
        valid_from = _utc(metadata.valid_from_utc)
        valid_until = _utc(metadata.valid_until_utc)
        if valid_until <= valid_from:
            reasons.append("POLICY_BUNDLE_VALIDITY_INVALID")
        elif now < valid_from or now >= valid_until:
            reasons.append("POLICY_BUNDLE_STALE")
    except (AttributeError, TypeError, ValueError):
        reasons.append("POLICY_BUNDLE_TIME_INVALID")

    parameters = asdict(bundle.parameters)
    if any(_finite_number(value) is None or float(value) < 0.0 for value in parameters.values()):
        reasons.append("POLICY_PARAMETERS_INVALID")
    return not reasons, list(dict.fromkeys(reasons))


def _availability_rule(
    evidence: Mapping[str, Any], scope: Applicability
) -> dict[str, Any]:
    if scope != "applicable":
        return _finding("R5", scope, "unknown" if scope == "unknown" else "not_evaluated", [])
    required = (
        "visual_watch_available",
        "auditory_watch_available",
        "radar_watch_available",
        "remote_supervisor_available",
        "evidence_fresh",
    )
    values = [_tri_bool(evidence.get(name)) for name in required]
    if any(value is None for value in values):
        return _finding("R5", "applicable", "unknown", ["LOOKOUT_EVIDENCE_INCOMPLETE"])
    if all(values):
        return _finding("R5", "applicable", "satisfied", ["LOOKOUT_PROXY_AVAILABLE"])
    return _finding("R5", "applicable", "not_satisfied", ["LOOKOUT_PROXY_UNAVAILABLE"])


def _safe_speed_rule(
    evidence: Mapping[str, Any], scope: Applicability, parameters: ShadowPolicyParameters
) -> dict[str, Any]:
    if scope != "applicable":
        return _finding("R6", scope, "unknown" if scope == "unknown" else "not_evaluated", [])
    speed = _finite_number(evidence.get("commanded_speed_mps"))
    stopping = _finite_number(evidence.get("stopping_distance_m"))
    clear = _finite_number(evidence.get("clear_distance_m"))
    traffic = _tri_bool(evidence.get("traffic_assessment_available"))
    if speed is None or stopping is None or clear is None or traffic is None:
        return _finding("R6", "applicable", "unknown", ["SAFE_SPEED_EVIDENCE_INCOMPLETE"])
    if min(speed, stopping, clear) < 0.0:
        return _finding("R6", "applicable", "unknown", ["SAFE_SPEED_EVIDENCE_INVALID"])
    within_speed = speed <= parameters.maximum_clear_visibility_speed_mps
    can_stop = stopping + parameters.stopping_distance_reserve_m <= clear
    if within_speed and can_stop and traffic:
        return _finding("R6", "applicable", "satisfied", ["SAFE_SPEED_PROXY_WITHIN_BOUNDS"])
    return _finding("R6", "applicable", "not_satisfied", ["SAFE_SPEED_PROXY_OUTSIDE_BOUNDS"])


def _normalized_angle(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def _near(value: float, boundary: float, band: float) -> bool:
    return abs(value - boundary) <= band


def _classify_encounter(
    encounter: Mapping[str, Any], parameters: ShadowPolicyParameters
) -> tuple[str | None, str | None, list[str]]:
    bearing = _finite_number(encounter.get("relative_bearing_deg"))
    reverse_bearing = _finite_number(encounter.get("ownship_bearing_from_contact_deg"))
    course_difference = _finite_number(encounter.get("course_difference_deg"))
    if bearing is None or reverse_bearing is None or course_difference is None:
        return None, None, ["ENCOUNTER_GEOMETRY_INCOMPLETE"]
    bearing = _normalized_angle(bearing)
    reverse_bearing = _normalized_angle(reverse_bearing)
    course_difference = abs(_normalized_angle(course_difference))
    band = parameters.classification_ambiguity_deg
    abaft = parameters.overtaking_abaft_beam_deg
    if (
        _near(abs(reverse_bearing), abaft, band)
        or _near(abs(bearing), parameters.head_on_bearing_tolerance_deg, band)
        or _near(
            course_difference,
            180.0 - parameters.reciprocal_course_tolerance_deg,
            band,
        )
    ):
        return None, None, ["ENCOUNTER_CLASSIFICATION_AMBIGUOUS"]
    if abs(reverse_bearing) > abaft:
        return "overtaking", "give_way", ["ENCOUNTER_OVERTAKING_PROXY"]
    head_on = (
        abs(bearing) < parameters.head_on_bearing_tolerance_deg
        and 180.0 - course_difference < parameters.reciprocal_course_tolerance_deg
    )
    if head_on:
        return "head_on", "give_way", ["ENCOUNTER_HEAD_ON_PROXY"]
    if _near(bearing, 0.0, band):
        return None, None, ["ENCOUNTER_CLASSIFICATION_AMBIGUOUS"]
    if bearing > 0.0:
        return "crossing_starboard", "give_way", ["ENCOUNTER_CROSSING_STARBOARD_PROXY"]
    return "crossing_port", "stand_on", ["ENCOUNTER_CROSSING_PORT_PROXY"]


def _action_status(
    encounter: Mapping[str, Any], parameters: ShadowPolicyParameters
) -> Finding:
    lead = _finite_number(encounter.get("action_lead_time_s"))
    course_change = _finite_number(encounter.get("course_change_deg"))
    speed_reduction = _finite_number(encounter.get("speed_reduction_mps"))
    detectable = _tri_bool(encounter.get("action_detectable"))
    if lead is None or course_change is None or speed_reduction is None or detectable is None:
        return "unknown"
    substantial = (
        abs(course_change) >= parameters.substantial_course_change_deg
        or speed_reduction >= parameters.substantial_speed_reduction_mps
    )
    return (
        "satisfied"
        if lead >= parameters.minimum_early_action_lead_s and substantial and detectable
        else "not_satisfied"
    )


def _encounter_rules(
    encounters: object, scope: Applicability, parameters: ShadowPolicyParameters
) -> list[dict[str, Any]]:
    if scope != "applicable":
        state: Finding = "unknown" if scope == "unknown" else "not_evaluated"
        return [_finding(rule, scope, state, []) for rule in ("R7", "R8", "R13", "R14", "R15", "R16", "R17")]
    if not isinstance(encounters, list):
        return [
            _finding(rule, "unknown", "unknown", ["ENCOUNTER_EVIDENCE_MISSING"])
            for rule in ("R7", "R8", "R13", "R14", "R15", "R16", "R17")
        ]
    if not encounters:
        return [
            _finding(rule, "not_applicable", "not_evaluated", ["NO_RELEVANT_ENCOUNTERS"])
            for rule in ("R7", "R8", "R13", "R14", "R15", "R16", "R17")
        ]

    results: list[dict[str, Any]] = []
    for index, raw in enumerate(encounters):
        if not isinstance(raw, Mapping):
            contact_id = f"encounter-{index}"
            results.extend(
                _finding(rule, "unknown", "unknown", ["ENCOUNTER_EVIDENCE_INVALID"], contact_id=contact_id)
                for rule in ("R7", "R8", "R13", "R14", "R15", "R16", "R17")
            )
            continue
        contact_id = str(raw.get("contact_id") or f"encounter-{index}")
        in_sight = _tri_bool(raw.get("in_sight"))
        power_driven = _tri_bool(raw.get("power_driven"))
        if in_sight is not True or power_driven is not True:
            applicability: Applicability = (
                "not_applicable" if in_sight is False or power_driven is False else "unknown"
            )
            state: Finding = "not_evaluated" if applicability == "not_applicable" else "unknown"
            reason = "CONTACT_OUTSIDE_RULE_SLICE" if applicability == "not_applicable" else "CONTACT_TYPE_UNKNOWN"
            results.extend(
                _finding(rule, applicability, state, [reason], contact_id=contact_id)
                for rule in ("R7", "R8", "R13", "R14", "R15", "R16", "R17")
            )
            continue

        doubt = _tri_bool(raw.get("risk_doubt"))
        treated = _tri_bool(raw.get("treated_as_collision_risk"))
        if doubt is None or treated is None:
            r7_status: Finding = "unknown"
            r7_reason = "RISK_ASSESSMENT_EVIDENCE_INCOMPLETE"
        elif doubt and not treated:
            r7_status = "not_satisfied"
            r7_reason = "DOUBT_NOT_TREATED_AS_RISK"
        else:
            r7_status = "satisfied"
            r7_reason = "DOUBT_TREATED_AS_RISK" if doubt else "RISK_ASSESSMENT_UNAMBIGUOUS"
        results.append(_finding("R7", "applicable", r7_status, [r7_reason], contact_id=contact_id))

        action_status = _action_status(raw, parameters) if treated is True else "not_evaluated"
        if treated is None:
            results.append(
                _finding("R8", "unknown", "unknown", ["COLLISION_RISK_UNKNOWN"], contact_id=contact_id)
            )
        elif treated is False:
            results.append(
                _finding("R8", "not_applicable", "not_evaluated", ["NO_COLLISION_RISK_IDENTIFIED"], contact_id=contact_id)
            )
        else:
            results.append(
                _finding(
                    "R8",
                    "applicable",
                    action_status,
                    [
                        "EARLY_SUBSTANTIAL_ACTION_PROXY_MET"
                        if action_status == "satisfied"
                        else "EARLY_SUBSTANTIAL_ACTION_PROXY_UNMET"
                        if action_status == "not_satisfied"
                        else "ACTION_EVIDENCE_INCOMPLETE"
                    ],
                    contact_id=contact_id,
                )
            )

        classification, duty, classification_reasons = _classify_encounter(raw, parameters)
        if classification is None or duty is None:
            results.extend(
                _finding(rule, "unknown", "unknown", classification_reasons, contact_id=contact_id)
                for rule in ("R13", "R14", "R15", "R16", "R17")
            )
            continue
        classification_rule = {
            "overtaking": "R13",
            "head_on": "R14",
            "crossing_starboard": "R15",
            "crossing_port": "R15",
        }[classification]
        for rule in ("R13", "R14", "R15"):
            results.append(
                _finding(
                    rule,
                    "applicable" if rule == classification_rule else "not_applicable",
                    "satisfied" if rule == classification_rule else "not_evaluated",
                    classification_reasons if rule == classification_rule else ["DIFFERENT_ENCOUNTER_CLASS"],
                    contact_id=contact_id,
                    duty=duty if rule == classification_rule else None,
                )
            )

        if duty == "give_way":
            maneuver_status = _action_status(raw, parameters)
            if classification == "head_on":
                course_change = _finite_number(raw.get("course_change_deg"))
                if course_change is None:
                    maneuver_status = "unknown"
                elif course_change <= 0.0:
                    maneuver_status = "not_satisfied"
            results.append(
                _finding(
                    "R16",
                    "applicable",
                    maneuver_status,
                    ["GIVE_WAY_ACTION_PROXY_EVALUATED"],
                    contact_id=contact_id,
                    duty="give_way",
                )
            )
            results.append(
                _finding(
                    "R17",
                    "not_applicable",
                    "not_evaluated",
                    ["OWNSHIP_NOT_STAND_ON_PROXY"],
                    contact_id=contact_id,
                )
            )
        else:
            maintained = _tri_bool(raw.get("course_and_speed_maintained"))
            status: Finding = (
                "unknown" if maintained is None else "satisfied" if maintained else "not_satisfied"
            )
            results.append(
                _finding(
                    "R16",
                    "not_applicable",
                    "not_evaluated",
                    ["OWNSHIP_NOT_GIVE_WAY_PROXY"],
                    contact_id=contact_id,
                )
            )
            results.append(
                _finding(
                    "R17",
                    "applicable",
                    status,
                    ["STAND_ON_ACTION_PROXY_EVALUATED"],
                    contact_id=contact_id,
                    duty="stand_on",
                )
            )
    return results


def _a5_status(governor_input: Mapping[str, Any], decision: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if decision.get("contract_type") != "AssuranceDecision" or decision.get("candidate_id") != "A5":
        reasons.append("A5_DECISION_MISSING_OR_INVALID")
    identities = (
        ("run_id", "run_id"),
        ("branch_id", "branch_id"),
        ("tick_index", "tick_index"),
    )
    for decision_key, input_key in identities:
        if decision.get(decision_key) != governor_input.get(input_key):
            reasons.append("A5_IDENTITY_MISMATCH")
            break
    snapshot = governor_input.get("snapshot")
    proposal = governor_input.get("proposal")
    if not isinstance(snapshot, Mapping) or decision.get("input_snapshot_id") != snapshot.get(
        "snapshot_id"
    ):
        reasons.append("A5_IDENTITY_MISMATCH")
    if not isinstance(proposal, Mapping) or decision.get("proposal_id") != proposal.get("command_id"):
        reasons.append("A5_IDENTITY_MISMATCH")
    if decision.get("valid") is not True:
        reasons.append("A5_DECISION_NOT_VALID")
    action = decision.get("action")
    if action in _A5_PERMISSIVE_ACTIONS and not isinstance(decision.get("issued_command"), Mapping):
        reasons.append("A5_ISSUED_COMMAND_MISSING")
    physically_permissive = not reasons and action in _A5_PERMISSIVE_ACTIONS
    if not physically_permissive:
        reasons.append("PHYSICAL_A5_SAFETY_DOMINATES")
    return physically_permissive, list(dict.fromkeys(reasons))


class A6PolicyShadow:
    """Evaluate a bounded policy shadow without control-path authority."""

    evaluator_version = "a6-singapore-policy-shadow-v1"

    def evaluate(
        self,
        *,
        governor_input: Mapping[str, Any],
        a5_decision: Mapping[str, Any],
        operational_context: Mapping[str, Any],
        evidence: Mapping[str, Any],
        bundle: PolicyBundle | None,
        evaluated_at_utc: str,
    ) -> dict[str, Any]:
        bundle_ok, bundle_reasons = _bundle_status(bundle, evaluated_at_utc)
        scope, scope_reasons = _scope(operational_context)
        a5_permissive, a5_reasons = _a5_status(governor_input, a5_decision)

        input_reasons: list[str] = []
        try:
            _canonical({"evidence": evidence, "operational_context": operational_context})
        except (TypeError, ValueError):
            input_reasons.append("SHADOW_INPUT_NOT_CANONICAL_JSON")
        if _contains_excluded_domain(operational_context) or _contains_excluded_domain(evidence):
            input_reasons.append("EXCLUDED_DOMAIN_PRESENT")

        snapshot = governor_input.get("snapshot")
        snapshot_contacts = snapshot.get("contacts") if isinstance(snapshot, Mapping) else None
        evidence_contacts = evidence.get("encounters")
        if isinstance(snapshot_contacts, list) and isinstance(evidence_contacts, list):
            expected_ids = [
                item.get("contact_id") for item in snapshot_contacts if isinstance(item, Mapping)
            ]
            observed_ids = [
                item.get("contact_id") for item in evidence_contacts if isinstance(item, Mapping)
            ]
            if (
                len(expected_ids) != len(snapshot_contacts)
                or len(observed_ids) != len(evidence_contacts)
                or len(set(expected_ids)) != len(expected_ids)
                or len(set(observed_ids)) != len(observed_ids)
                or set(expected_ids) != set(observed_ids)
            ):
                input_reasons.append("ENCOUNTER_EVIDENCE_IDENTITY_MISMATCH")
        else:
            input_reasons.append("ENCOUNTER_EVIDENCE_INCOMPLETE")

        bundle_summary: dict[str, Any] | None = None
        if bundle is not None:
            bundle_summary = {
                "bundle_id": bundle.metadata.bundle_id,
                "version": bundle.metadata.version,
                "content_sha256": bundle.metadata.content_sha256,
                "source_pins": [asdict(item) for item in bundle.metadata.source_pins],
            }

        if not bundle_ok or bundle is None or input_reasons:
            unavailable_reasons = [*bundle_reasons, *input_reasons]
            findings = [
                _finding(rule, "unknown", "unknown", unavailable_reasons)
                for rule in ("R5", "R6", "R7", "R8", "R13", "R14", "R15", "R16", "R17")
            ]
        else:
            findings = [
                _availability_rule(
                    evidence.get("lookout", {})
                    if isinstance(evidence.get("lookout"), Mapping)
                    else {},
                    scope,
                ),
                _safe_speed_rule(
                    evidence.get("safe_speed", {})
                    if isinstance(evidence.get("safe_speed"), Mapping)
                    else {},
                    scope,
                    bundle.parameters,
                ),
                *_encounter_rules(evidence.get("encounters"), scope, bundle.parameters),
            ]

        unresolved = any(
            item["applicability"] == "unknown"
            or item["finding"] in {"unknown", "not_satisfied"}
            for item in findings
        )
        supported = (
            bundle_ok
            and not input_reasons
            and scope == "applicable"
            and not unresolved
            and a5_permissive
        )
        reason_codes = list(
            dict.fromkeys((*bundle_reasons, *input_reasons, *scope_reasons, *a5_reasons))
        )
        if unresolved:
            reason_codes.append("SHADOW_FINDING_UNRESOLVED")
        if not supported:
            reason_codes.append("SHADOW_SUPPORT_WITHHELD")

        digest_input = {
            "a5_decision_id": a5_decision.get("decision_id"),
            "bundle": bundle_summary,
            "evaluated_at_utc": evaluated_at_utc,
            "evaluator_version": self.evaluator_version,
            "evidence": evidence if not input_reasons else "INVALID_NON_CANONICAL_INPUT",
            "findings": findings,
            "operational_context": (
                operational_context if not input_reasons else "INVALID_NON_CANONICAL_INPUT"
            ),
        }
        assessment_id = f"a6-shadow:{hashlib.sha256(_canonical(digest_input)).hexdigest()}"
        return {
            "record_type": "A6PolicyShadowAssessment",
            "schema_version": "local-a6-shadow-v1",
            "assessment_id": assessment_id,
            "evaluator_version": self.evaluator_version,
            "observation_only": True,
            "control_authority": "none",
            "legal_compliance_determination": False,
            "evaluated_at_utc": evaluated_at_utc,
            "run_id": governor_input.get("run_id"),
            "branch_id": governor_input.get("branch_id"),
            "tick_index": governor_input.get("tick_index"),
            "a5": {
                "decision_id": a5_decision.get("decision_id"),
                "action": a5_decision.get("action"),
                "valid": a5_decision.get("valid"),
                "physical_safety_dominates": not a5_permissive,
            },
            "bundle": bundle_summary,
            "scope_applicability": scope if bundle_ok else "unknown",
            "shadow_support": "supported" if supported else "withheld",
            "reason_codes": reason_codes,
            "findings": findings,
            "limitations": [
                "ENGINEERING_POLICY_PROXY_NOT_LEGAL_COMPLIANCE",
                "CLEAR_VISIBILITY_DAYLIGHT_SLICE_ONLY",
                "RESTRICTED_VISIBILITY_UNSUPPORTED",
                "MASS_CODE_DESIGN_ASSURANCE_REFERENCE_ONLY_NOT_RUNTIME_LAW",
                "WEAPONS_TARGETING_CLASSIFIED_DOCTRINE_AND_ROE_EXCLUDED",
            ],
        }
