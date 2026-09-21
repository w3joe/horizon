"""Validated private recipes for the source-plan S01--S22 catalogue."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

from .scenario import Scenario, load_scenario


KNOWN_AI_POLICIES = frozenset(
    {"nominal", "unsafe_straight", "expired", "stale_lineage", "malformed"}
)
KNOWN_ACTIONS = frozenset(
    {
        "delay_link",
        "disconnect_link",
        "flood_diagnostics",
        "operator_acknowledge",
        "restart_service",
        "restore_link",
        "run_perception_variant",
        "set_diagnostic_mode",
        "stop_service",
    }
)
KNOWN_STATUSES = frozenset(
    {"simulator_executable", "integration_recipe", "external_artifact_required"}
)


@dataclass(frozen=True)
class OrchestratorStep:
    at_s: float
    action: str
    target: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class SourcePlanRecipe:
    source_plan_id: str
    title: str
    physical_fixtures: tuple[Scenario, ...]
    decision_ai_policies: tuple[str, ...]
    fixture_status: str
    orchestrator_steps: tuple[OrchestratorStep, ...]
    dependencies: tuple[str, ...]
    acceptance_condition: str
    independent_verification: str
    source_path: Path


def load_source_plan_recipe(path: str | Path) -> SourcePlanRecipe:
    """Load one recipe and validate every locally executable reference."""

    source_path = Path(path).resolve()
    raw = json.loads(source_path.read_text())
    if raw.get("schema_version") != "source-plan-recipe/0.1.0":
        raise ValueError("unsupported source-plan recipe schema")
    if raw.get("private_evaluation_configuration") is not True:
        raise ValueError("source-plan recipes must remain private evaluation configuration")
    source_plan_id = str(raw["source_plan_id"])
    if len(source_plan_id) != 3 or not source_plan_id.startswith("S"):
        raise ValueError(f"invalid source-plan ID: {source_plan_id}")
    case_number = int(source_plan_id[1:])
    if not 1 <= case_number <= 22:
        raise ValueError(f"source-plan ID outside S01--S22: {source_plan_id}")

    scenarios_root = source_path.parent.parent.resolve()
    fixtures: list[Scenario] = []
    for relative in raw.get("physical_fixtures", []):
        fixture_path = (scenarios_root / str(relative)).resolve()
        if fixture_path.parent != scenarios_root:
            raise ValueError("physical fixture must be a direct child of scenarios/")
        fixtures.append(load_scenario(fixture_path))
    if not fixtures:
        raise ValueError(f"{source_plan_id} needs at least one physical fixture")

    policies = tuple(str(item) for item in raw.get("decision_ai_policies", []))
    if not policies or any(item not in KNOWN_AI_POLICIES for item in policies):
        raise ValueError(f"{source_plan_id} names an unsupported decision-AI policy")

    status = str(raw.get("fixture_status"))
    if status not in KNOWN_STATUSES:
        raise ValueError(f"{source_plan_id} has invalid fixture_status")

    steps: list[OrchestratorStep] = []
    previous_time = -1.0
    for item in raw.get("orchestrator_steps", []):
        at_s = float(item["at_s"])
        action = str(item["action"])
        target = str(item["target"])
        if not math.isfinite(at_s) or at_s < 0.0 or at_s < previous_time:
            raise ValueError(f"{source_plan_id} orchestrator times must be finite and ordered")
        if action not in KNOWN_ACTIONS or not target:
            raise ValueError(f"{source_plan_id} has an unsupported orchestrator step")
        parameters = item.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError(f"{source_plan_id} step parameters must be an object")
        steps.append(OrchestratorStep(at_s, action, target, dict(parameters)))
        previous_time = at_s

    dependencies = tuple(str(item) for item in raw.get("dependencies", []))
    if status != "simulator_executable" and not dependencies:
        raise ValueError(f"{source_plan_id} must name its integration dependencies")
    acceptance = str(raw.get("acceptance_condition", "")).strip()
    if not acceptance:
        raise ValueError(f"{source_plan_id} needs an acceptance condition")
    verification = str(raw.get("independent_verification", ""))
    if verification != "pending_A08":
        raise ValueError("independent verification must remain explicitly pending A08")
    return SourcePlanRecipe(
        source_plan_id=source_plan_id,
        title=str(raw["title"]),
        physical_fixtures=tuple(fixtures),
        decision_ai_policies=policies,
        fixture_status=status,
        orchestrator_steps=tuple(steps),
        dependencies=dependencies,
        acceptance_condition=acceptance,
        independent_verification=verification,
        source_path=source_path,
    )


def load_source_plan_catalog(directory: str | Path) -> tuple[SourcePlanRecipe, ...]:
    """Load the complete catalogue, rejecting missing or duplicate cases."""

    recipes = tuple(load_source_plan_recipe(path) for path in sorted(Path(directory).glob("s*.json")))
    identifiers = [item.source_plan_id for item in recipes]
    expected = [f"S{index:02d}" for index in range(1, 23)]
    if identifiers != expected:
        raise ValueError(f"source-plan catalogue must contain exactly S01--S22, got {identifiers}")
    return recipes
