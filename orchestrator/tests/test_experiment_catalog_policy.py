"""Regression coverage for experiment catalogue and operational policies."""

from pathlib import Path

import pytest

from raidar.application.execution import (
    BENCHMARK_EXPERIMENTS_ROOT_NAME,
    OPERATIONAL_SMOKE_EXPERIMENTS_ROOT_NAME,
    resolve_experiments_root,
)
from raidar.application.scenario_catalog import (
    load_scenario,
    validate_scenario_experiment_kind,
)
from raidar.matrix import (
    AgentSpecInput,
    ExperimentConfig,
    MatrixConfig,
    MatrixEntryInput,
    resolve_matrix_jobs,
)


def _write_scenario(
    scenario_root: Path,
    revision: str,
    *,
    name: str,
    evaluation_role: str = "benchmark",
    catalog_status: str = "active",
) -> Path:
    scenario_path = scenario_root / revision / "scenario.yaml"
    scenario_path.parent.mkdir(parents=True)
    scenario_path.write_text(
        "\n".join(
            [
                f"name: {name}",
                f"display_name: {name.replace('-', ' ').title()}",
                f"scenario_revision: {revision}",
                "parent_revision: null",
                "description: Temporary experiment catalogue scenario",
                "difficulty: easy",
                "task_kind: feature_implementation",
                "delivery_mode: direct_implementation",
                "workspace_state: new",
                "starting_conditions: []",
                "languages:",
                "  - python",
                f"evaluation_role: {evaluation_role}",
                f"catalog_status: {catalog_status}",
                "timeout_sec: 300",
                "environment:",
                "  kind: stack_preset",
                "  id: node:20",
                "  workdir: /app",
                "  requirements:",
                "    runtimes:",
                '      node: ">=20"',
                "    package_managers:",
                '      bun: ">=1"',
                "    tools:",
                '      typescript: ">=5"',
                "    browsers: {}",
                "  resources:",
                "    cpus: 2",
                "    memory_mb: 4096",
                "    storage_mb: 10240",
                "  allow_internet: true",
                "starter:",
                "  root: temporary",
                "verification:",
                "  min_quality_score: 0.0",
                "  gates: []",
                "  required_commands: []",
                "requirements:",
                "  items: []",
                "scorers:",
                "  - id: resource-efficiency",
                "    version: 1",
                "    weight: 1.0",
                "prompt:",
                "  entry: prompt/task.md",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return scenario_path


def _matrix_config(scenario_root: Path, entries: list[MatrixEntryInput]) -> MatrixConfig:
    return MatrixConfig(
        id="catalogue-policy",
        scenario=scenario_root,
        experiment=ExperimentConfig(timeout_sec=300, repeats=1, repeat_parallel=1, retry_void=0),
        entries=entries,
    )


def _matrix_entry(entry_id: str, revision: str) -> MatrixEntryInput:
    return MatrixEntryInput(
        id=entry_id,
        scenario_revision=revision,
        agent=AgentSpecInput(harness="codex-cli", provider="openai", model="gpt-5.4"),
    )


def test_active_benchmark_scenario_resolves_for_benchmark_execution(tmp_path: Path) -> None:
    scenario_path = _write_scenario(
        tmp_path / "scenarios" / "active-benchmark",
        "v001",
        name="active-benchmark",
    )

    scenario = load_scenario(scenario_path)

    validate_scenario_experiment_kind(scenario, experiment_kind="benchmark")
    assert (
        resolve_experiments_root(
            experiments_root=None,
            experiment_kind="benchmark",
            repo_root=tmp_path,
        )
        == tmp_path / "experiments" / BENCHMARK_EXPERIMENTS_ROOT_NAME
    )


@pytest.mark.parametrize("catalog_status", ["calibration", "quarantined"])
def test_non_active_benchmark_scenarios_are_rejected_before_execution(
    tmp_path: Path, catalog_status: str
) -> None:
    scenario = load_scenario(
        _write_scenario(
            tmp_path / "scenarios" / catalog_status,
            "v001",
            name=f"{catalog_status}-benchmark",
            catalog_status=catalog_status,
        )
    )

    with pytest.raises(ValueError, match=rf"catalog_status='{catalog_status}'"):
        validate_scenario_experiment_kind(scenario, experiment_kind="benchmark")


def test_operational_scenarios_require_operational_smoke_and_its_storage_root(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(
        _write_scenario(
            tmp_path / "scenarios" / "operational",
            "v001",
            name="operational-check",
            evaluation_role="operational",
        )
    )

    for experiment_kind in ("benchmark", "research-loop"):
        with pytest.raises(ValueError, match="evaluation_role='operational'"):
            validate_scenario_experiment_kind(scenario, experiment_kind=experiment_kind)

    validate_scenario_experiment_kind(scenario, experiment_kind="operational-smoke")
    assert (
        resolve_experiments_root(
            experiments_root=None,
            experiment_kind="operational-smoke",
            repo_root=tmp_path,
        )
        == tmp_path / "experiments" / OPERATIONAL_SMOKE_EXPERIMENTS_ROOT_NAME
    )


@pytest.mark.parametrize(
    ("entry_id", "evaluation_role", "catalog_status", "expected_condition"),
    [
        ("operational-entry", "operational", "active", "evaluation_role='operational'"),
        ("quarantined-entry", "benchmark", "quarantined", "catalog_status='quarantined'"),
    ],
)
def test_benchmark_matrix_rejects_ineligible_entries_with_entry_and_scenario_identity(
    tmp_path: Path,
    entry_id: str,
    evaluation_role: str,
    catalog_status: str,
    expected_condition: str,
) -> None:
    scenario_name = f"{entry_id}-scenario"
    scenario_root = tmp_path / "scenarios" / scenario_name
    _write_scenario(
        scenario_root,
        "v001",
        name=scenario_name,
        evaluation_role=evaluation_role,
        catalog_status=catalog_status,
    )
    config = _matrix_config(scenario_root, [_matrix_entry(entry_id, "v001")])

    with pytest.raises(
        ValueError,
        match=rf"Matrix entry '{entry_id}' scenario '{scenario_name}'.*{expected_condition}",
    ):
        resolve_matrix_jobs(config, repo_root=tmp_path, experiment_kind="benchmark")
