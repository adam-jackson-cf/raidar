"""Tests for CLI utility commands and helpers under the scenario migration."""

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import click
from click.testing import CliRunner

from raidar.cli import (
    RunCliOptions,
    _assert_no_generated_artifact_changes,
    _generated_artifact_paths,
    _persist_experiment_execution,
    main,
)
from raidar.schemas.scenario import ScenarioDefinition
from raidar.schemas.scorecard import EvalConfig, EvalRun, Scorecard


def test_cli_version_matches_pyproject_version() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])

    assert result.exit_code == 0, result.output
    pyproject_data = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    expected_version = pyproject_data["project"]["version"]
    assert result.output.strip().endswith(expected_version)


def test_harness_list_includes_model_variations() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["harness", "list"])

    assert result.exit_code == 0, result.output
    assert "claude-code  -> CLAUDE.md" in result.output
    assert (
        "models: anthropic/claude-haiku-4-5, anthropic/claude-opus-4-6, "
        "anthropic/claude-sonnet-4-5, anthropic/claude-sonnet-4-6"
    ) in result.output
    assert (
        "models: codex/* (known aliases: codex/gpt-5.2-high, codex/gpt-5.2-low, "
        "codex/gpt-5.2-medium, codex/gpt-5.4-extra-high, codex/gpt-5.4-high, "
        "codex/gpt-5.4-low, codex/gpt-5.4-medium)"
    ) in result.output
    assert (
        "models: google/gemini-3-flash-preview, google/gemini-3-pro-preview, "
        "google/gemini-3.1-pro-preview"
    ) in result.output
    assert "models: cursor/*, openai/*, anthropic/*, google/*, deepseek/*" in result.output
    assert "models: github/*" in result.output
    assert "models: inflection/*" in result.output


def test_scenario_list_returns_scenario_ids_with_revisions(tmp_path: Path) -> None:
    runner = CliRunner()
    scenarios_root = tmp_path / "scenarios"
    alpha_root = scenarios_root / "alpha-task"
    beta_root = scenarios_root / "beta-task"

    alpha_v1 = runner.invoke(
        main,
        [
            "scenario",
            "init",
            "--path",
            str(alpha_root),
            "--name",
            "alpha-task",
            "--scenario-revision",
            "v001",
        ],
    )
    assert alpha_v1.exit_code == 0, alpha_v1.output

    alpha_v2 = runner.invoke(
        main,
        [
            "scenario",
            "init",
            "--path",
            str(alpha_root),
            "--name",
            "alpha-task",
            "--scenario-revision",
            "v002",
        ],
    )
    assert alpha_v2.exit_code == 0, alpha_v2.output

    beta_v1 = runner.invoke(
        main,
        [
            "scenario",
            "init",
            "--path",
            str(beta_root),
            "--name",
            "beta-task",
            "--scenario-revision",
            "v001",
        ],
    )
    assert beta_v1.exit_code == 0, beta_v1.output

    result = runner.invoke(main, ["scenario", "list", "--scenarios-root", str(scenarios_root)])

    assert result.exit_code == 0, result.output
    assert result.output.strip().splitlines() == [
        "alpha-task | revisions: v001, v002",
        "beta-task | revisions: v001",
    ]


def test_generated_artifact_paths_filters_prefixes() -> None:
    paths = [
        "experiments/20260220-000000Z__hello-world-smoke__v001/runs/run-01/run.json",
        "scenarios/hello-world-smoke/v001/scenario.yaml",
        "orchestrator/src/raidar/cli.py",
    ]

    matches = _generated_artifact_paths(paths)

    assert matches == [
        "experiments/20260220-000000Z__hello-world-smoke__v001/runs/run-01/run.json",
    ]


def test_run_cli_options_resolved_caps_retry_and_resolves_paths(tmp_path: Path) -> None:
    options = RunCliOptions(
        scenario=tmp_path / "scenario.yaml",
        harness="gemini",
        model="google/gemini-3-flash-preview",
        timeout=300,
        repeats=5,
        repeat_parallel=2,
        rerun_unscored=7,
    )

    resolved = options.resolved()

    assert resolved.rerun_unscored == 1
    assert resolved.scenario.is_absolute()


def test_experiment_run_uses_harness_model_execution_suffix(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text("name: placeholder\n")
    captured: dict[str, object] = {}

    def fake_execute_run_options(options, **kwargs):
        captured["options"] = options
        captured.update(kwargs)

    monkeypatch.setattr("raidar.cli._execute_run_options", fake_execute_run_options)

    result = runner.invoke(
        main,
        [
            "experiment",
            "run",
            "--scenario",
            str(scenario_path),
            "--harness",
            "codex-cli",
            "--model",
            "codex/gpt-5.4-high",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["force_experiment_summary"] is True
    assert captured["cleanup_before_runs"] is True
    assert captured["echo"] is True
    assert captured["execution_suffix"] == "codex-cli__codex-gpt-5.4-high"


def test_scenario_init_creates_schema_valid_scenario_and_rules(tmp_path: Path) -> None:
    runner = CliRunner()
    task_dir = tmp_path / "scenarios" / "sample-task"

    result = runner.invoke(
        main,
        ["scenario", "init", "--path", str(task_dir), "--name", "sample-task"],
    )

    assert result.exit_code == 0, result.output
    scenario_yaml = task_dir / "v001" / "scenario.yaml"
    assert scenario_yaml.exists()

    scenario_def = ScenarioDefinition.from_yaml(scenario_yaml)
    assert scenario_def.name == "sample-task"
    assert scenario_def.scenario_revision == "v001"
    assert scenario_def.starter.root == "starter"
    assert scenario_def.prompt.entry == "prompt/task.md"
    assert scenario_def.verification.required_commands == [
        ["bun", "run", "typecheck"],
        ["bun", "run", "lint"],
    ]
    assert [gate.command for gate in scenario_def.verification.gates] == [
        ["bun", "run", "typecheck"],
        ["bun", "run", "lint"],
    ]
    assert scenario_def.metric_ids() == [
        "functional",
        "acceptance",
        "verification-stability",
        "execution-validity",
        "resource-efficiency",
    ]

    rules_dir = task_dir / "v001" / "rules"
    assert (rules_dir / "AGENTS.md").exists()
    assert (rules_dir / "CLAUDE.md").exists()
    assert (rules_dir / "GEMINI.md").exists()
    assert (rules_dir / "copilot-instructions.md").exists()
    assert (rules_dir / "user-rules-setting.md").exists()
    assert (task_dir / "v001" / "prompt" / "task.md").exists()


def test_artifact_guard_allows_generated_artifact_deletions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "raidar.cli._changed_repo_entries",
        lambda _: [
            (
                "D",
                "experiments/20260220-000000Z__hello-world-smoke__v001/runs/run-01/run.json",
            ),
        ],
    )

    _assert_no_generated_artifact_changes(tmp_path)


def test_artifact_guard_rejects_modified_generated_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "raidar.cli._changed_repo_entries",
        lambda _: [
            (
                "M",
                "experiments/20260220-000000Z__hello-world-smoke__v001/runs/run-01/run.json",
            ),
            (
                "A",
                "experiments/20260220-000000Z__hello-world-smoke__v001/report.md",
            ),
        ],
    )

    try:
        _assert_no_generated_artifact_changes(tmp_path)
    except click.ClickException as exc:
        assert "Generated Harbor artifacts must not be committed" in str(exc)
    else:
        raise AssertionError("Expected generated artifact guard failure.")


def _create_starter_files(scenario_dir: Path, revision: str) -> None:
    starter_dir = scenario_dir / revision / "starter"
    src_dir = starter_dir / "src" / "app"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "page.tsx").write_text("export default function Page() { return null; }\n")
    (starter_dir / "package.json").write_text(
        json.dumps({"dependencies": {}, "devDependencies": {}})
    )
    (starter_dir / "tsconfig.json").write_text("{}\n")
    (starter_dir / "next.config.ts").write_text("export default {};\n")
    (starter_dir / "postcss.config.mjs").write_text("export default {};\n")


def test_scenario_clone_revision_auto_increments(tmp_path: Path) -> None:
    runner = CliRunner()
    scenario_dir = tmp_path / "scenarios" / "sample-task"

    init_result = runner.invoke(
        main,
        ["scenario", "init", "--path", str(scenario_dir), "--name", "sample-task"],
    )
    assert init_result.exit_code == 0, init_result.output

    _create_starter_files(scenario_dir, revision="v001")

    clone_result = runner.invoke(
        main,
        ["scenario", "clone-revision", "--path", str(scenario_dir), "--from-revision", "v001"],
    )
    assert clone_result.exit_code == 0, clone_result.output
    assert "target_revision: v002" in clone_result.output

    cloned_scenario_yaml = scenario_dir / "v002" / "scenario.yaml"
    cloned_scenario = ScenarioDefinition.from_yaml(cloned_scenario_yaml)
    assert cloned_scenario.scenario_revision == "v002"
    assert (scenario_dir / "v002" / "starter" / "src" / "app" / "page.tsx").exists()


def test_scenario_clone_revision_succeeds_without_starter_manifest(tmp_path: Path) -> None:
    runner = CliRunner()
    scenario_dir = tmp_path / "scenarios" / "sample-task"

    init_result = runner.invoke(
        main,
        ["scenario", "init", "--path", str(scenario_dir), "--name", "sample-task"],
    )
    assert init_result.exit_code == 0, init_result.output

    (scenario_dir / "v001" / "starter").mkdir(parents=True, exist_ok=True)

    clone_result = runner.invoke(
        main,
        ["scenario", "clone-revision", "--path", str(scenario_dir), "--from-revision", "v001"],
    )
    assert clone_result.exit_code == 0, clone_result.output
    assert (scenario_dir / "v002").exists()


def test_info_selects_latest_scenario_revision_numerically(tmp_path: Path) -> None:
    runner = CliRunner()
    scenario_dir = tmp_path / "scenarios" / "sample-task"

    init_v2 = runner.invoke(
        main,
        [
            "scenario",
            "init",
            "--path",
            str(scenario_dir),
            "--name",
            "sample-task",
            "--scenario-revision",
            "v2",
        ],
    )
    assert init_v2.exit_code == 0, init_v2.output

    init_v10 = runner.invoke(
        main,
        [
            "scenario",
            "init",
            "--path",
            str(scenario_dir),
            "--name",
            "sample-task",
            "--scenario-revision",
            "v10",
        ],
    )
    assert init_v10.exit_code == 0, init_v10.output

    info_result = runner.invoke(main, ["info", "--scenario", str(scenario_dir)])
    assert info_result.exit_code == 0, info_result.output
    assert "Revision: v10" in info_result.output
    assert f"Scenario YAML: {scenario_dir / 'v10' / 'scenario.yaml'}" in info_result.output
    assert "Available Revisions:" in info_result.output
    assert f"  v2: {scenario_dir / 'v2' / 'scenario.yaml'}" in info_result.output
    assert f"  v10: {scenario_dir / 'v10' / 'scenario.yaml'}" in info_result.output
    assert (
        "Evaluation Profile: "
        "v2:functional+acceptance+verification-stability+execution-validity+resource-efficiency"
        in info_result.output
    )


def _write_experiment_summary(
    execution_dir: Path,
    *,
    scenario_name: str,
    model: str,
    harness: str,
    evaluation_profile: str,
    created_at: str,
    run_count_total: int = 1,
    unscored_count: int = 0,
) -> None:
    execution_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at_utc": created_at,
        "config": {
            "scenario_name": scenario_name,
            "harness": harness,
            "model": model,
            "evaluation_profile": evaluation_profile,
        },
        "aggregate": {
            "run_count_total": run_count_total,
            "unscored_count": unscored_count,
        },
    }
    (execution_dir / "experiment-summary.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_experiments_list_filters_and_json_output(tmp_path: Path) -> None:
    runner = CliRunner()
    experiments_root = tmp_path / "experiments"
    _write_experiment_summary(
        experiments_root / "20260222-100000Z__hello-world-smoke__v001",
        scenario_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        evaluation_profile="v2:functional+acceptance+verification-stability+execution-validity+resource-efficiency",
        created_at="2026-02-22T10:00:00+00:00",
    )
    _write_experiment_summary(
        experiments_root / "20260222-110000Z__homepage-implementation__v001",
        scenario_name="homepage-implementation",
        model="codex/gpt-5.4-high",
        harness="codex-cli",
        evaluation_profile="v2:functional+acceptance+verification-stability+execution-validity+resource-efficiency+visual-regression",
        created_at="2026-02-22T11:00:00+00:00",
    )

    text_result = runner.invoke(
        main,
        [
            "experiments",
            "list",
            "--experiments-root",
            str(experiments_root),
            "--scenario",
            "homepage",
        ],
    )
    assert text_result.exit_code == 0, text_result.output
    assert "homepage-implementation@v001" in text_result.output
    assert "hello-world-smoke@v001" not in text_result.output
    assert (
        "evaluation_profile=v2:functional+acceptance+verification-stability+execution-validity+resource-efficiency+visual-regression"
        in text_result.output
    )

    profile_result = runner.invoke(
        main,
        [
            "experiments",
            "list",
            "--experiments-root",
            str(experiments_root),
            "--evaluation-profile",
            "visual-regression",
            "--json",
        ],
    )
    assert profile_result.exit_code == 0, profile_result.output
    profile_rows = json.loads(profile_result.output)
    assert len(profile_rows) == 1
    assert profile_rows[0]["scenario_name"] == "homepage-implementation"

    json_result = runner.invoke(
        main,
        [
            "experiments",
            "list",
            "--experiments-root",
            str(experiments_root),
            "--json",
        ],
    )
    assert json_result.exit_code == 0, json_result.output
    rows = json.loads(json_result.output)
    assert isinstance(rows, list)
    assert rows[0]["execution_id"] == "20260222-110000Z__homepage-implementation__v001"
    assert rows[0]["evaluation_profile"] == (
        "v2:functional+acceptance+verification-stability+execution-validity+resource-efficiency+visual-regression"
    )


def test_experiments_prune_keeps_latest_per_model(tmp_path: Path) -> None:
    runner = CliRunner()
    experiments_root = tmp_path / "experiments"
    archive_root = tmp_path / "archive"

    old_dir = experiments_root / "20260220-100000Z__hello-world-smoke__v001"
    new_dir = experiments_root / "20260221-100000Z__hello-world-smoke__v001"
    other_model_dir = experiments_root / "20260222-100000Z__hello-world-smoke__v001"
    _write_experiment_summary(
        old_dir,
        scenario_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        evaluation_profile="v2:functional+acceptance+verification-stability+execution-validity+resource-efficiency",
        created_at="2026-02-20T10:00:00+00:00",
    )
    _write_experiment_summary(
        new_dir,
        scenario_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        evaluation_profile="v2:functional+acceptance+verification-stability+execution-validity+resource-efficiency",
        created_at="2026-02-21T10:00:00+00:00",
    )
    _write_experiment_summary(
        other_model_dir,
        scenario_name="hello-world-smoke",
        model="codex/gpt-5.4-high",
        harness="codex-cli",
        evaluation_profile="v2:functional+acceptance+verification-stability+execution-validity+resource-efficiency",
        created_at="2026-02-22T10:00:00+00:00",
    )

    result = runner.invoke(
        main,
        [
            "experiments",
            "prune",
            "--experiments-root",
            str(experiments_root),
            "--archive-dir",
            str(archive_root),
            "--keep-per-model",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert new_dir.exists()
    assert other_model_dir.exists()
    assert not old_dir.exists()
    assert (archive_root / "experiments" / old_dir.name).exists()
    assert "experiments_pruned=1" in result.output


def test_experiments_prune_dry_run_does_not_move_directories(tmp_path: Path) -> None:
    runner = CliRunner()
    experiments_root = tmp_path / "experiments"
    archive_root = tmp_path / "archive"
    old_dir = experiments_root / "20260220-100000Z__hello-world-smoke__v001"
    new_dir = experiments_root / "20260221-100000Z__hello-world-smoke__v001"
    _write_experiment_summary(
        old_dir,
        scenario_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        evaluation_profile="v2:functional+acceptance+verification-stability+execution-validity+resource-efficiency",
        created_at="2026-02-20T10:00:00+00:00",
    )
    _write_experiment_summary(
        new_dir,
        scenario_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        evaluation_profile="v2:functional+acceptance+verification-stability+execution-validity+resource-efficiency",
        created_at="2026-02-21T10:00:00+00:00",
    )

    result = runner.invoke(
        main,
        [
            "experiments",
            "prune",
            "--experiments-root",
            str(experiments_root),
            "--archive-dir",
            str(archive_root),
            "--keep-per-model",
            "1",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert old_dir.exists()
    assert new_dir.exists()
    assert not archive_root.exists()
    assert "would-archive: experiments/" in result.output


def test_persist_experiment_execution_passes_reruns_used(monkeypatch, tmp_path: Path) -> None:
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "hello-world-smoke",
            "scenario_revision": "v001",
            "description": "Smoke scenario",
            "difficulty": "easy",
            "category": "smoke",
            "timeout_sec": 300,
            "starter": {"root": "starter"},
            "verification": {
                "gates": [],
                "required_commands": [],
            },
            "acceptance": {},
            "metrics": [{"type": "core", "id": "functional"}],
            "prompt": {"entry": "prompt/task.md"},
        }
    )
    options = RunCliOptions(
        scenario=tmp_path / "scenario.yaml",
        harness="codex-cli",
        model="codex/gpt-5.4-high",
        timeout=300,
        repeats=1,
        repeat_parallel=1,
        rerun_unscored=1,
    )
    request = type("Request", (), {"scenario": scenario})()
    run = EvalRun(
        id="run-01",
        timestamp="2026-03-10T13:00:00+00:00",
        config=EvalConfig(
            model="codex/gpt-5.4-high",
            harness="codex-cli",
            scenario_name="hello-world-smoke",
            scenario_revision="v001",
            starter_root="starter",
            evaluation_profile="v2:functional",
        ),
        duration_sec=1.0,
        scores=Scorecard(),
    )

    captured: dict[str, object] = {}

    def fake_runner_api():
        return type(
            "RunnerApi",
            (),
            {
                "scenario_evaluation_profile": staticmethod(lambda _scenario: "v2:functional"),
                "scenario_metrics": staticmethod(lambda _scenario: ["functional"]),
            },
        )()

    def fake_experiment_api():
        def create_experiment_summary(**kwargs):
            captured.update(kwargs)
            return {"experiment_id": "exp-01"}

        return type(
            "ExperimentApi",
            (),
            {
                "create_experiment_summary": staticmethod(create_experiment_summary),
                "persist_experiment": staticmethod(
                    lambda *_args, **_kwargs: (
                        tmp_path / "experiment.json",
                        tmp_path / "experiment-summary.json",
                        tmp_path / "report.md",
                    )
                ),
            },
        )()

    monkeypatch.setattr("raidar.cli._runner_api", fake_runner_api)
    monkeypatch.setattr("raidar.cli._experiment_api", fake_experiment_api)

    _persist_experiment_execution(
        resolved=options,
        request=request,
        scenario_def=scenario,
        execution_dir=tmp_path / "experiments" / "exp-01",
        started_at=datetime(2026, 3, 10, 13, 0, 0, tzinfo=UTC),
        runs=[run],
        retries_used=1,
        unresolved_unscored=0,
        echo=False,
    )

    assert captured["reruns_used"] == 1
    assert "retries_used" not in captured
