"""Scenario loading and metadata services."""

from __future__ import annotations

from pathlib import Path

import yaml

from raidar.schemas.scenario import ScenarioDefinition


def load_scenario(path: Path) -> ScenarioDefinition:
    """Load one scenario definition."""

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return ScenarioDefinition.model_validate(data)


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
