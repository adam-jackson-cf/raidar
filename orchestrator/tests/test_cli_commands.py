"""Tests for CLI utility commands and helpers under the scenario migration."""

import json
import os
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import click
from click.testing import CliRunner

from raidar.cli import (
    BENCHMARK_EXPERIMENTS_ROOT,
    ORCHESTRATOR_ROOT,
    RESEARCH_LOOP_EXPERIMENTS_ROOT,
    RunCliOptions,
    SuiteExecutionResult,
    _archive_destination,
    _assert_no_generated_artifact_changes,
    _generated_artifact_paths,
    _persist_experiment_execution,
    _resolve_experiments_root,
    main,
    quality_gates,
)
from raidar.schemas.scenario import ScenarioDefinition
from raidar.schemas.scorecard import EvalConfig, EvalRun, Scorecard


def _assert_smoke_dry_run_output(output: str) -> None:
    assert "uv run --project orchestrator raidar run \\" in output
    assert "uv run --project orchestrator raidar matrix \\" in output
    assert '--selector "all" \\' in output
    assert '--repeats "2" \\' in output
    assert '--repeat-parallel "2" \\' in output
    assert "uv run --project orchestrator raidar harbor cleanup" in output
    assert "uv run --project orchestrator raidar harness validate \\" in output
    assert "uv run --project orchestrator raidar experiment run \\" in output
    assert 'RESEARCH_SMOKE_OBJECTIVE_ID="research-smoke-dry-run"' in output
    assert "research-smoke-init" in output
    assert "research-smoke-approve" in output
    assert "research-smoke-cleanup" in output
    assert "objective_id=research-smoke-dry-run" in output
    assert "uv run --project auto_researcher auto-researcher init \\" in output
    assert '--loop-execution-mode "parallel" \\' in output
    assert '--max-parallel-loops "2" \\' in output
    assert '--benchmark-repeat-parallel "2" \\' in output
    assert '--research-repeat-parallel "2" \\' in output
    assert "uv run --project auto_researcher auto-researcher approve-scenario" in output


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
        "codex/gpt-5.4-low, codex/gpt-5.4-medium, codex/gpt-5.4-mini)"
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

    alpha_revision_two = runner.invoke(
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
    assert alpha_revision_two.exit_code == 0, alpha_revision_two.output

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
    assert resolved.experiments_root.is_absolute()


def test_resolve_experiments_root_uses_kind_defaults() -> None:
    benchmark_root = _resolve_experiments_root(experiments_root=None, experiment_kind="benchmark")
    research_root = _resolve_experiments_root(
        experiments_root=None,
        experiment_kind="research-loop",
    )

    assert benchmark_root == BENCHMARK_EXPERIMENTS_ROOT
    assert research_root == RESEARCH_LOOP_EXPERIMENTS_ROOT


def test_resolve_experiments_root_prefers_explicit_path(tmp_path: Path) -> None:
    explicit = tmp_path / "custom-root"

    resolved = _resolve_experiments_root(
        experiments_root=explicit,
        experiment_kind="research-loop",
    )

    assert resolved == explicit.resolve()


def test_quality_gates_writes_coverage_to_pytest_cache(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setattr("raidar.cli._assert_no_generated_artifact_changes", lambda repo_root: None)
    monkeypatch.setattr("raidar.cli._has_unstaged_changes", lambda repo_root: False)
    monkeypatch.setattr("raidar.cli.shutil.which", lambda command: "/usr/bin/lizard")

    def fake_run_or_raise(cmd, cwd, *, env=None):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})

    monkeypatch.setattr("raidar.cli._run_or_raise", fake_run_or_raise)

    quality_gates.callback(fix=False, stage=False)

    coverage_call = next(call for call in calls if "--cov=src" in call["cmd"])
    coverage_env = coverage_call["env"]
    assert isinstance(coverage_env, dict)
    assert coverage_env["COVERAGE_FILE"] == str(
        ORCHESTRATOR_ROOT / ".pytest_cache" / "coverage" / ".coverage"
    )


def test_env_setup_uses_frozen_sync(monkeypatch) -> None:
    runner = CliRunner()
    calls: list[dict[str, object]] = []

    monkeypatch.setattr("raidar.cli._cleanup_stale_harbor_before_runs", lambda: None)
    monkeypatch.setattr(
        "raidar.cli._runner_api",
        lambda: type(
            "FakeRunnerApi",
            (),
            {"_docker_compose_preflight_reason": staticmethod(lambda env: None)},
        )(),
    )

    def fake_run_or_raise(cmd, cwd, *, env=None):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})

    monkeypatch.setattr("raidar.cli._run_or_raise", fake_run_or_raise)
    monkeypatch.setattr(
        "raidar.cli.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="harbor 0.0.0\n",
            stderr="",
        ),
    )

    result = runner.invoke(main, ["env", "setup", "--sync-arg", "--all-extras"])

    assert result.exit_code == 0, result.output
    assert calls[0]["cmd"] == ["uv", "python", "install", "3.12"]
    assert calls[1]["cmd"] == ["uv", "sync", "--frozen", "--all-extras"]
    assert calls[2]["cmd"] == ["uv", "tool", "install", "harbor"]


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
    assert captured["options"].repeats == 5


def test_experiment_run_routes_research_loop_kind(tmp_path: Path, monkeypatch) -> None:
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
            "--experiment-kind",
            "research-loop",
        ],
    )

    assert result.exit_code == 0, result.output
    options = captured["options"]
    assert isinstance(options, RunCliOptions)
    assert options.experiments_root == RESEARCH_LOOP_EXPERIMENTS_ROOT


def test_experiment_run_json_emits_machine_readable_payload(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text("name: placeholder\n")

    run = EvalRun(
        id="run-01",
        timestamp="2026-03-10T13:00:00+00:00",
        config=EvalConfig(
            model="codex/gpt-5.4-high",
            harness="codex-cli",
            scenario_name="sample-task",
            scenario_revision="v001",
            starter_root="starter",
            evaluation_profile="functional",
        ),
        duration_sec=1.0,
        scores=Scorecard(
            metadata={
                "run": {
                    "canonical_run_dir": str(tmp_path / "runs" / "run-01"),
                    "run_json_path": str(tmp_path / "runs" / "run-01" / "run.json"),
                }
            }
        ),
    )

    def fake_execute_run_options(options, **kwargs):
        assert kwargs["echo"] is False
        return SuiteExecutionResult(
            scenario_path=options.scenario,
            scenario_name="sample-task",
            scenario_revision="v001",
            runs=[run],
            retries_used=1,
            experiment_json_path=tmp_path / "experiment.json",
            summary_path=tmp_path / "experiment-summary.json",
            report_path=tmp_path / "report.md",
        )

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
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary_path"] == str(tmp_path / "experiment-summary.json")
    assert payload["retries_used"] == 1
    assert payload["runs"][0]["run_json_path"] == str(tmp_path / "runs" / "run-01" / "run.json")


def test_matrix_dry_run_supports_selector_generation(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text("name: placeholder\n")
    scenario = ScenarioDefinition.model_validate(
        {
            "name": "hello-world-smoke",
            "scenario_revision": "v001",
            "description": "Smoke scenario",
            "difficulty": "easy",
            "category": "smoke",
            "timeout_sec": 300,
            "starter": {"root": "starter"},
            "verification": {"gates": [], "required_commands": []},
            "acceptance": {},
            "metrics": [{"type": "core", "id": "functional"}],
            "prompt": {"entry": "prompt/task.md"},
        }
    )

    monkeypatch.setattr(
        "raidar.cli._load_matrix_scenarios",
        lambda scenario_paths: [(scenario_paths[0], scenario)],
    )

    result = runner.invoke(
        main,
        [
            "matrix",
            "--scenario",
            str(scenario_path),
            "--selector",
            "gemini",
            "--repeats",
            "2",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Generating matrix from selector 'gemini'" in result.output
    assert "Matrix defined for 3 agent specs (3 experiments)" in result.output
    assert (
        "[dry-run] hello-world-smoke@v001: gemini/google/gemini-3-flash-preview x2" in result.output
    )


def test_matrix_requires_exactly_one_of_config_or_selector(tmp_path: Path) -> None:
    runner = CliRunner()
    scenario_path = tmp_path / "scenario.yaml"
    config_path = tmp_path / "matrix.yaml"
    model_name = "codex/gpt-5.4-high"
    scenario_path.write_text("name: placeholder\n")
    config_path.write_text(
        "\n".join(
            [
                "matrix:",
                "  experiment:",
                "    timeout_sec: 1800",
                "    repeats: 5",
                "    repeat_parallel: 1",
                "    retry_void: 0",
                "  agents:",
                "    - harness: codex-cli",
                f"      model: {model_name}",
            ]
        )
        + "\n"
    )

    result = runner.invoke(
        main,
        [
            "matrix",
            "--scenario",
            str(scenario_path),
            "--config",
            str(config_path),
            "--selector",
            "codex",
        ],
    )

    assert result.exit_code != 0
    assert "Provide exactly one of --config or --selector." in result.output


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


def test_scenario_init_json_emits_machine_readable_payload(tmp_path: Path) -> None:
    runner = CliRunner()
    task_dir = tmp_path / "scenarios" / "json-task"

    result = runner.invoke(
        main,
        [
            "scenario",
            "init",
            "--path",
            str(task_dir),
            "--name",
            "json-task",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["scenario_name"] == "json-task"
    assert payload["scenario_revision"] == "v001"
    assert Path(payload["scenario_yaml"]).is_file()
    assert Path(payload["prompt_path"]).is_file()
    assert Path(payload["rules_dir"]).is_dir()


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


def test_scenario_clone_revision_json_emits_machine_readable_payload(tmp_path: Path) -> None:
    runner = CliRunner()
    scenario_dir = tmp_path / "scenarios" / "json-clone-task"

    init_result = runner.invoke(
        main,
        ["scenario", "init", "--path", str(scenario_dir), "--name", "json-clone-task"],
    )
    assert init_result.exit_code == 0, init_result.output

    clone_result = runner.invoke(
        main,
        [
            "scenario",
            "clone-revision",
            "--path",
            str(scenario_dir),
            "--from-revision",
            "v001",
            "--json",
        ],
    )

    assert clone_result.exit_code == 0, clone_result.output
    payload = json.loads(clone_result.output)
    assert payload["source_revision"] == "v001"
    assert payload["target_revision"] == "v002"
    assert Path(payload["scenario_yaml"]).is_file()


def test_info_selects_latest_scenario_revision_numerically(tmp_path: Path) -> None:
    runner = CliRunner()
    scenario_dir = tmp_path / "scenarios" / "sample-task"

    init_revision_two = runner.invoke(
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
    assert init_revision_two.exit_code == 0, init_revision_two.output

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
        "functional+acceptance+verification-stability+execution-validity+resource-efficiency"
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
        evaluation_profile="functional+acceptance+verification-stability+execution-validity+resource-efficiency",
        created_at="2026-02-22T10:00:00+00:00",
    )
    _write_experiment_summary(
        experiments_root / "20260222-110000Z__homepage-implementation__v001",
        scenario_name="homepage-implementation",
        model="codex/gpt-5.4-high",
        harness="codex-cli",
        evaluation_profile="functional+acceptance+verification-stability+execution-validity+resource-efficiency+visual-regression",
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
        "evaluation_profile=functional+acceptance+verification-stability+execution-validity+resource-efficiency+visual-regression"
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
        "functional+acceptance+verification-stability+execution-validity+resource-efficiency+visual-regression"
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
        evaluation_profile="functional+acceptance+verification-stability+execution-validity+resource-efficiency",
        created_at="2026-02-20T10:00:00+00:00",
    )
    _write_experiment_summary(
        new_dir,
        scenario_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        evaluation_profile="functional+acceptance+verification-stability+execution-validity+resource-efficiency",
        created_at="2026-02-21T10:00:00+00:00",
    )
    _write_experiment_summary(
        other_model_dir,
        scenario_name="hello-world-smoke",
        model="codex/gpt-5.4-high",
        harness="codex-cli",
        evaluation_profile="functional+acceptance+verification-stability+execution-validity+resource-efficiency",
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
    assert _archive_destination(old_dir, archive_root).exists()
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
        evaluation_profile="functional+acceptance+verification-stability+execution-validity+resource-efficiency",
        created_at="2026-02-20T10:00:00+00:00",
    )
    _write_experiment_summary(
        new_dir,
        scenario_name="hello-world-smoke",
        model="anthropic/claude-haiku-4-5",
        harness="claude-code",
        evaluation_profile="functional+acceptance+verification-stability+execution-validity+resource-efficiency",
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
    expected_rel = _archive_destination(old_dir, archive_root).relative_to(archive_root)
    assert f"would-archive: {expected_rel}" in result.output


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
            evaluation_profile="functional",
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
                "scenario_evaluation_profile": staticmethod(lambda _scenario: "functional"),
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


def test_run_agent_smoke_script_uses_make_targets(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "checks" / "run-agent-smoke.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_log = tmp_path / "make.log"
    fake_make = bin_dir / "make"
    fake_make.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                'printf \'ARGS:%s\\n\' "$*" >> "$FAKE_MAKE_LOG"',
                (
                    "printf 'ENV:%s:%s\\n' "
                    '"${HARBOR_SMOKE_FAST:-}" '
                    '"${HARBOR_SMOKE_FAST_REUSE_IMAGE:-}" >> "$FAKE_MAKE_LOG"'
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_make.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_MAKE_LOG"] = str(make_log)

    result = subprocess.run(
        [
            "bash",
            str(script_path),
            "--harness",
            "codex-cli",
            "--timeout",
            "120",
            "--repeats",
            "2",
            "--repeat-parallel",
            "3",
            "--rerun-unscored",
            "1",
            "--fast",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert make_log.read_text(encoding="utf-8").splitlines() == [
        (
            f"ARGS:-C {repo_root} agent-smoke HARNESS=codex-cli "
            "MODEL=codex/gpt-5.4-mini TIMEOUT_SEC=120 "
            "AGENT_SMOKE_SCENARIO=scenarios/hello-world-smoke/v001/scenario.yaml "
            "AGENT_SMOKE_REPEATS=2 AGENT_SMOKE_REPEAT_PARALLEL=3 "
            "AGENT_SMOKE_RERUN_UNSCORED=1"
        ),
        "ENV:1:1",
    ]


def test_orchestrator_smoke_make_target_supports_repeat_overrides(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_log = tmp_path / "make.log"

    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                'printf \'DOCKER:%s\\n\' "$*" >> "$FAKE_MAKE_LOG"',
                'if [ "$1" = "info" ]; then',
                "  exit 0",
                "fi",
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                'printf \'UV:%s\\n\' "$*" >> "$FAKE_MAKE_LOG"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_MAKE_LOG"] = str(make_log)

    result = subprocess.run(
        [
            "make",
            "orchestrator-smoke",
            "ORCHESTRATOR_SMOKE_REPEATS=2",
            "RUN_PARALLELISM=2",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert make_log.read_text(encoding="utf-8").splitlines() == [
        "DOCKER:info",
        (
            "UV:run --project orchestrator raidar run "
            "--scenario scenarios/hello-world-smoke/v001/scenario.yaml "
            "--harness codex-cli --model codex/gpt-5.4-mini "
            "--repeats 2 --repeat-parallel 2 --rerun-unscored 0 "
            "--experiment-kind benchmark"
        ),
    ]


def test_smoke_matrix_make_target_uses_default_smoke_scenario(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_log = tmp_path / "make.log"

    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                'printf \'DOCKER:%s\\n\' "$*" >> "$FAKE_MAKE_LOG"',
                'if [ "$1" = "info" ]; then',
                "  exit 0",
                "fi",
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                'printf \'UV:%s\\n\' "$*" >> "$FAKE_MAKE_LOG"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_MAKE_LOG"] = str(make_log)

    result = subprocess.run(
        ["make", "smoke-matrix"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert make_log.read_text(encoding="utf-8").splitlines() == [
        "DOCKER:info",
        (
            "UV:run --project orchestrator raidar matrix "
            "--scenario scenarios/hello-world-smoke/v001/scenario.yaml "
            "--selector all --repeats 1 --repeat-parallel 1 "
            "--rerun-unscored 0 --experiment-kind benchmark"
        ),
    ]


def test_smoke_dry_run_check_prints_all_public_smoke_shapes() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        ["make", "smoke-dry-run-check"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    _assert_smoke_dry_run_output(result.stdout)


def test_research_smoke_make_target_forwards_parallel_shape(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "Makefile").write_text(
        (repo_root / "Makefile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_log = tmp_path / "make.log"

    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                'printf \'DOCKER:%s\\n\' "$*" >> "$FAKE_MAKE_LOG"',
                'if [ "$1" = "info" ]; then',
                "  exit 0",
                "fi",
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                'printf \'UV:%s\\n\' "$*" >> "$FAKE_MAKE_LOG"',
                'project=""',
                'tool=""',
                'subcmd=""',
                'if [ "$1" = "run" ] && [ "$2" = "--project" ]; then',
                '  project="$3"',
                '  tool="$4"',
                '  subcmd="$5"',
                "fi",
                'objective_id=""',
                'loop_execution_mode=""',
                'max_parallel_loops=""',
                'benchmark_repeats=""',
                'benchmark_repeat_parallel=""',
                'research_repeats=""',
                'research_repeat_parallel=""',
                'previous=""',
                'for arg in "$@"; do',
                '  case "$previous" in',
                '    objective_id) objective_id="$arg"; previous=""; continue ;;',
                ('    loop_execution_mode) loop_execution_mode="$arg"; previous=""; continue ;;'),
                '    max_parallel_loops) max_parallel_loops="$arg"; previous=""; continue ;;',
                '    benchmark_repeats) benchmark_repeats="$arg"; previous=""; continue ;;',
                (
                    '    benchmark_repeat_parallel) benchmark_repeat_parallel="$arg"; '
                    'previous=""; continue ;;'
                ),
                '    research_repeats) research_repeats="$arg"; previous=""; continue ;;',
                (
                    '    research_repeat_parallel) research_repeat_parallel="$arg"; '
                    'previous=""; continue ;;'
                ),
                "  esac",
                '  case "$arg" in',
                '    --objective-id) previous="objective_id" ;;',
                '    --loop-execution-mode) previous="loop_execution_mode" ;;',
                '    --max-parallel-loops) previous="max_parallel_loops" ;;',
                '    --benchmark-repeats) previous="benchmark_repeats" ;;',
                '    --benchmark-repeat-parallel) previous="benchmark_repeat_parallel" ;;',
                '    --research-repeats) previous="research_repeats" ;;',
                '    --research-repeat-parallel) previous="research_repeat_parallel" ;;',
                "  esac",
                "done",
                (
                    'if [ "$project" = "auto_researcher" ] && '
                    '[ "$tool" = "auto-researcher" ] && [ "$subcmd" = "init" ]; then'
                ),
                '  objective_root="$PWD/auto_researcher/objectives/$objective_id"',
                '  mkdir -p "$objective_root"',
                (
                    "  printf 'INIT:%s:%s:%s:%s:%s:%s\\n' \"$loop_execution_mode\" "
                    '"$max_parallel_loops" "$benchmark_repeats" '
                    '"$benchmark_repeat_parallel" "$research_repeats" '
                    '"$research_repeat_parallel" >> "$FAKE_MAKE_LOG"'
                ),
                '  cat > "$objective_root/objective.yaml" <<EOF',
                "objective_id: $objective_id",
                "status: awaiting_scenario_approval",
                "scenario_slug: research-smoke-fake",
                "EOF",
                "  exit 0",
                "fi",
                (
                    'if [ "$project" = "auto_researcher" ] && '
                    '[ "$tool" = "auto-researcher" ] && '
                    '[ "$subcmd" = "approve-scenario" ]; then'
                ),
                '  objective_root="$PWD/auto_researcher/objectives/$objective_id"',
                '  scenario_root="$PWD/scenarios/research-smoke-fake"',
                '  benchmark_root="$PWD/experiments/benchmarks/research-smoke-fake"',
                '  mkdir -p "$scenario_root" "$benchmark_root"',
                '  : > "$benchmark_root/experiment-summary.json"',
                '  cat > "$objective_root/objective.yaml" <<EOF',
                "objective_id: $objective_id",
                "status: active",
                "scenario_slug: research-smoke-fake",
                "best_benchmark_ref: "
                "$PWD"
                "/experiments/benchmarks/research-smoke-fake/experiment-summary.json",
                "EOF",
                '  printf \'APPROVE:%s\\n\' "$objective_id" >> "$FAKE_MAKE_LOG"',
                "  exit 0",
                "fi",
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_MAKE_LOG"] = str(make_log)

    result = subprocess.run(
        [
            "make",
            "research-smoke",
            "RESEARCH_SMOKE_LOOP_EXECUTION_MODE=parallel",
            "RESEARCH_SMOKE_MAX_PARALLEL_LOOPS=2",
            "RESEARCH_SMOKE_BENCHMARK_REPEATS=2",
            "RESEARCH_SMOKE_BENCHMARK_REPEAT_PARALLEL=2",
            "RESEARCH_SMOKE_RESEARCH_REPEATS=2",
            "RESEARCH_SMOKE_RESEARCH_REPEAT_PARALLEL=2",
        ],
        cwd=workspace_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = make_log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "DOCKER:info"
    assert (
        "UV:run --project auto_researcher auto-researcher init" in lines[1]
        and "--loop-execution-mode parallel" in lines[1]
        and "--max-parallel-loops 2" in lines[1]
        and "--benchmark-repeats 2" in lines[1]
        and "--benchmark-repeat-parallel 2" in lines[1]
        and "--research-repeats 2" in lines[1]
        and "--research-repeat-parallel 2" in lines[1]
    )
    assert "INIT:parallel:2:2:2:2:2" in lines
    assert any(
        line.startswith("UV:run --project auto_researcher auto-researcher approve-scenario")
        for line in lines
    )
    approve_line = next(line for line in lines if line.startswith("APPROVE:research-smoke-"))
    objective_id = approve_line.split(":", 1)[1]
    assert approve_line.startswith("APPROVE:research-smoke-")
    assert not (workspace_root / "auto_researcher" / "objectives" / objective_id).exists()
    assert not (workspace_root / "scenarios" / "research-smoke-fake").exists()
    assert not (workspace_root / "experiments" / "benchmarks" / "research-smoke-fake").exists()
