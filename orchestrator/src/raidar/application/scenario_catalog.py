"""Scenario loading and metadata services."""

from __future__ import annotations

from pathlib import Path

import yaml

from raidar.schemas.scenario import ScenarioDefinition


def is_benchmark_catalogue_scenario(scenario: ScenarioDefinition) -> bool:
    """Return whether a scenario is eligible for benchmark catalogue selection."""

    return scenario.evaluation_role == "benchmark" and scenario.catalog_status == "active"


def benchmark_catalogue_scenarios(
    scenarios: list[ScenarioDefinition],
) -> tuple[ScenarioDefinition, ...]:
    """Return only scenarios eligible for benchmark catalogue selection."""

    return tuple(scenario for scenario in scenarios if is_benchmark_catalogue_scenario(scenario))


def scenario_experiment_kind_condition(
    scenario: ScenarioDefinition,
    *,
    experiment_kind: str,
) -> str | None:
    """Return the policy condition preventing a scenario from running in a kind."""

    condition = None
    is_operational_smoke = experiment_kind == "operational-smoke"
    if experiment_kind == "benchmark" and scenario.evaluation_role != "benchmark":
        condition = f"evaluation_role={scenario.evaluation_role!r}"
    elif experiment_kind == "benchmark" and scenario.catalog_status != "active":
        condition = f"catalog_status={scenario.catalog_status!r}"
    elif experiment_kind == "research-loop" and scenario.evaluation_role == "operational":
        condition = "evaluation_role='operational' requires experiment kind 'operational-smoke'"
    elif is_operational_smoke and scenario.evaluation_role != "operational":
        condition = (
            f"evaluation_role={scenario.evaluation_role!r} requires experiment kind "
            "'benchmark' or 'research-loop'"
        )
    elif experiment_kind not in {"benchmark", "research-loop", "operational-smoke"}:
        condition = f"experiment kind {experiment_kind!r} is unsupported"
    return condition


def validate_scenario_experiment_kind(
    scenario: ScenarioDefinition,
    *,
    experiment_kind: str,
) -> None:
    """Validate a scenario against the requested experiment kind."""

    condition = scenario_experiment_kind_condition(
        scenario,
        experiment_kind=experiment_kind,
    )
    if condition is not None:
        raise ValueError(condition)


def load_scenario(path: Path) -> ScenarioDefinition:
    """Load one scenario definition."""

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return ScenarioDefinition.model_validate(data)


def save_scenario(path: Path, scenario: ScenarioDefinition) -> None:
    """Persist one validated scenario definition with stable key ordering."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            scenario.model_dump(mode="json", exclude_none=True),
            handle,
            sort_keys=False,
        )


def scenario_evaluation_profile(scenario: ScenarioDefinition) -> str:
    """Return the scenario evaluation profile label."""

    from raidar.scorers.registry import scorer_evaluation_profile

    return scorer_evaluation_profile(scenario)


def scenario_metrics(scenario: ScenarioDefinition) -> tuple[str, ...]:
    """Return the scenario metric identifiers."""

    return tuple(scenario.metric_ids())


def scenario_scorers(scenario: ScenarioDefinition) -> tuple[str, ...]:
    """Return the scenario scorer identifiers."""

    return tuple(scenario.scorer_ids())
