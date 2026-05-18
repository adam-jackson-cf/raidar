"""Scenario loading and metadata services."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scenario import ScenarioDefinition


def load_scenario(path: Path) -> ScenarioDefinition:
    """Load one scenario definition."""

    from raidar import runner

    return runner.load_scenario(path)


def scenario_evaluation_profile(scenario: ScenarioDefinition) -> str:
    """Return the scenario evaluation profile label."""

    from raidar import runner

    return runner.scenario_evaluation_profile(scenario)


def scenario_metrics(scenario: ScenarioDefinition) -> tuple[str, ...]:
    """Return the scenario metric identifiers."""

    from raidar import runner

    return runner.scenario_metrics(scenario)
