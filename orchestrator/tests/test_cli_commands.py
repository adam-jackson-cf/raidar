"""Tests for CLI utility commands and helpers under the scenario migration."""

import pytest

from tests import cli_commands_support as support

json = support.json
os = support.os
subprocess = support.subprocess
tomllib = support.tomllib
dataclass = support.dataclass
UTC = support.UTC
datetime = support.datetime
Path = support.Path
click = support.click
CliRunner = support.CliRunner
execution = support.execution
ExecutionDispatchRequest = support.ExecutionDispatchRequest
RunCliOptions = support.RunCliOptions
assert_no_generated_artifact_changes = support.assert_no_generated_artifact_changes
generated_artifact_paths = support.generated_artifact_paths
BENCHMARK_EXPERIMENTS_ROOT = support.BENCHMARK_EXPERIMENTS_ROOT
ORCHESTRATOR_ROOT = support.ORCHESTRATOR_ROOT
RESEARCH_LOOP_EXPERIMENTS_ROOT = support.RESEARCH_LOOP_EXPERIMENTS_ROOT
SuiteExecutionResult = support.SuiteExecutionResult
_archive_destination = support._archive_destination
_resolve_experiments_root = support._resolve_experiments_root
main = support.main
quality_gates = support.quality_gates
ScenarioDefinition = support.ScenarioDefinition
EvalConfig = support.EvalConfig
EvalRun = support.EvalRun
Scorecard = support.Scorecard

DEFAULT_EXPERIMENT_PROFILE = (
    "functional+requirements-coverage+verification-stability+execution-validity+resource-efficiency"
)


@dataclass(frozen=True)
class ExperimentSummaryFixture:
    execution_dir: Path
    scenario_name: str
    model: str
    harness: str
    created_at: str
    evaluation_profile: str = DEFAULT_EXPERIMENT_PROFILE
    run_count_total: int = 1
    unscored_count: int = 0


@dataclass(frozen=True)
class FakeCommandEnv:
    repo_root: Path
    make_log: Path
    env: dict[str, str]


@dataclass(frozen=True)
class PruneExperimentDirs:
    old_dir: Path
    new_dir: Path
    other_model_dir: Path


def _run_options(tmp_path: Path, **overrides: object) -> RunCliOptions:
    values = {
        "scenario": tmp_path / "scenario.yaml",
        "harness": "codex-cli",
        "provider": "openai",
        "model": "gpt-5.4",
        "reasoning_effort": "high",
        "timeout": 300,
        "repeats": 1,
        "repeat_parallel": 1,
        "rerun_unscored": 1,
    }
    values.update(overrides)
    return RunCliOptions(**values)


def _assert_smoke_dry_run_output(output: str) -> None:
    assert "uv run --project orchestrator raidar run \\" in output
    assert "uv run --project orchestrator raidar matrix \\" in output
    assert '--config "matrices/hello-world-smoke-trio.yaml" \\' in output
    assert '--repeats "2" \\' in output
    assert '--repeat-parallel "2" \\' in output
    assert "uv run --project orchestrator raidar experiment run \\" in output


def _init_scenario_revision(
    runner: CliRunner, scenario_root: Path, name: str, revision: str
) -> None:
    result = runner.invoke(
        main,
        [
            "scenario",
            "init",
            "--path",
            str(scenario_root),
            "--name",
            name,
            "--scenario-revision",
            revision,
        ],
    )
    assert result.exit_code == 0, result.output


def _sample_machine_readable_run(tmp_path: Path) -> EvalRun:
    run_dir = tmp_path / "runs" / "run-01"
    return EvalRun(
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
                    "canonical_run_dir": str(run_dir),
                    "run_json_path": str(run_dir / "run.json"),
                }
            }
        ),
    )


def _invoke_experiment_run_json(runner: CliRunner, scenario_path: Path):
    return runner.invoke(
        main,
        [
            "experiment",
            "run",
            "--scenario",
            str(scenario_path),
            "--harness",
            "codex-cli",
            "--provider",
            "openai",
            "--model",
            "gpt-5.4",
            "--reasoning-effort",
            "high",
            "--json",
        ],
    )


def _fake_command_env(tmp_path: Path, *, codex_auth_mode: bool = False) -> FakeCommandEnv:
    repo_root = Path(__file__).resolve().parents[2]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_log = tmp_path / "make.log"
    _write_fake_docker(bin_dir)
    _write_fake_uv(bin_dir, codex_auth_mode=codex_auth_mode)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_MAKE_LOG"] = str(make_log)
    env.pop("CODEX_AUTH_MODE", None)
    return FakeCommandEnv(repo_root=repo_root, make_log=make_log, env=env)


def _fake_make_script_env(tmp_path: Path) -> FakeCommandEnv:
    fake_env = _fake_command_env(tmp_path)
    _write_fake_make(tmp_path / "bin")
    return fake_env


def _write_fake_docker(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "docker",
        [
            "#!/bin/sh",
            'printf \'DOCKER:%s\\n\' "$*" >> "$FAKE_MAKE_LOG"',
            'if [ "$1" = "info" ]; then',
            "  exit 0",
            "fi",
            "exit 0",
        ],
    )


def _write_fake_uv(bin_dir: Path, *, codex_auth_mode: bool = False) -> None:
    env_line = (
        "printf 'ENV:%s:%s:%s\\n' "
        '"${RAIDAR_TASK_IMAGE_REUSE:-}" '
        '"${RAIDAR_TASK_IMAGE_REUSE:-}" '
        '"${CODEX_AUTH_MODE:-}" >> "$FAKE_MAKE_LOG"'
    )
    if not codex_auth_mode:
        env_line = (
            "printf 'ENV:%s:%s\\n' "
            '"${RAIDAR_TASK_IMAGE_REUSE:-}" '
            '"${RAIDAR_TASK_IMAGE_REUSE:-}" >> "$FAKE_MAKE_LOG"'
        )
    _write_executable(
        bin_dir / "uv",
        ["#!/bin/sh", 'printf \'UV:%s\\n\' "$*" >> "$FAKE_MAKE_LOG"', env_line],
    )


def _write_fake_make(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "make",
        [
            "#!/bin/sh",
            'printf \'ARGS:%s\\n\' "$*" >> "$FAKE_MAKE_LOG"',
            (
                "printf 'ENV:%s:%s\\n' "
                '"${RAIDAR_TASK_IMAGE_REUSE:-}" '
                '"${RAIDAR_TASK_IMAGE_REUSE:-}" >> "$FAKE_MAKE_LOG"'
            ),
        ],
    )


def _write_executable(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


def _run_with_fake_commands(fake_env: FakeCommandEnv, args: list[str]):
    return subprocess.run(
        args,
        cwd=fake_env.repo_root,
        env=fake_env.env,
        capture_output=True,
        text=True,
        check=False,
    )


def _minimal_cli_scenario() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "name": "hello-world-smoke",
            "scenario_revision": "v001",
            "description": "Smoke scenario",
            "difficulty": "easy",
            "category": "smoke",
            "timeout_sec": 300,
            "starter": {"root": "starter"},
            "verification": {"gates": [], "required_commands": [], "min_quality_score": 0.0},
            "requirements": {"items": []},
            "scorers": [{"id": "resource-efficiency", "version": 1, "weight": 1.0}],
            "prompt": {"entry": "prompt/task.md"},
        }
    )


def _minimal_eval_run() -> EvalRun:
    return EvalRun(
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


def _dispatch_execute_run_command(options: RunCliOptions, repo_root: Path) -> None:
    execution.execute_run_command(
        ExecutionDispatchRequest(
            options=options,
            force_experiment_summary=True,
            cleanup_before_runs=True,
            echo=False,
        ),
        repo_root=repo_root,
    )


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
        "models: anthropic/claude-haiku-4-5, anthropic/claude-opus-4-6, anthropic/claude-opus-4-7, "
        "anthropic/claude-sonnet-4-5, anthropic/claude-sonnet-4-6"
    ) in result.output
    assert (
        "models: openai/gpt-5.2, openai/gpt-5.3-codex-spark, openai/gpt-5.4, "
        "openai/gpt-5.4-mini, openai/gpt-5.5"
    ) in result.output
    assert (
        "models: google/gemini-3-flash-preview, google/gemini-3-pro-preview, "
        "google/gemini-3.1-pro-preview"
    ) in result.output
    assert "models: cursor/*, openai/*, anthropic/*, google/*, deepseek/*" in result.output
    assert "models: github/*" in result.output
    assert "models: inflection/*" in result.output


def test_harness_validate_reports_codex_auth_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))

    result = runner.invoke(
        main,
        [
            "harness",
            "validate",
            "--harness",
            "codex-cli",
            "--provider",
            "openai",
            "--model",
            "gpt-5.4-mini",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "auth_mode: api" in result.output
    assert "auth_mode_requested: auto" in result.output
    assert "auth_source: OPENAI_API_KEY" in result.output


def test_harness_setup_auth_is_noop_when_auth_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    (auth_dir / "auth.json").write_text('{"access_token":"token"}', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(auth_dir))

    result = runner.invoke(main, ["harness", "setup-auth", "--harness", "codex-cli"])

    assert result.exit_code == 0, result.output
    assert "Codex auth is already configured." in result.output
    assert f"auth_json_path: {auth_dir / 'auth.json'}" in result.output


def test_harness_setup_auth_runs_codex_login_with_device_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    auth_path = auth_dir / "auth.json"
    monkeypatch.setenv("CODEX_HOME", str(auth_dir))
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    commands: list[list[str]] = []

    def fake_run(command, check=False):
        del check
        commands.append(command)
        auth_path.write_text('{"access_token":"token"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("raidar.commands.harness.subprocess.run", fake_run)

    result = runner.invoke(
        main,
        ["harness", "setup-auth", "--harness", "codex-cli", "--device-auth"],
    )

    assert result.exit_code == 0, result.output
    assert commands == [["/usr/local/bin/codex", "login", "--device-auth"]]
    assert "Codex auth setup complete." in result.output


def test_harness_setup_auth_fails_when_login_does_not_create_auth_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(auth_dir))
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")

    def fake_run(command, check=False):
        del check
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("raidar.commands.harness.subprocess.run", fake_run)

    result = runner.invoke(main, ["harness", "setup-auth", "--harness", "codex-cli"])

    assert result.exit_code != 0
    assert "no file-backed auth.json was found" in result.output


def test_scenario_list_returns_scenario_ids_with_revisions(tmp_path: Path) -> None:
    runner = CliRunner()
    scenarios_root = tmp_path / "scenarios"
    alpha_root = scenarios_root / "alpha-task"
    beta_root = scenarios_root / "beta-task"

    _init_scenario_revision(runner, alpha_root, "alpha-task", "v001")
    _init_scenario_revision(runner, alpha_root, "alpha-task", "v002")
    _init_scenario_revision(runner, beta_root, "beta-task", "v001")

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

    matches = generated_artifact_paths(paths)

    assert matches == [
        "experiments/20260220-000000Z__hello-world-smoke__v001/runs/run-01/run.json",
    ]


def test_run_cli_options_resolved_caps_retry_and_resolves_paths(tmp_path: Path) -> None:
    options = _run_options(
        tmp_path,
        harness="gemini",
        provider="google",
        model="gemini-3-flash-preview",
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

    monkeypatch.setattr(
        "raidar.commands.quality.repo_state.assert_no_generated_artifact_changes",
        lambda repo_root: None,
    )
    monkeypatch.setattr(
        "raidar.commands.quality.repo_state.has_unstaged_changes", lambda repo_root: False
    )
    monkeypatch.setattr("raidar.commands.quality.shutil.which", lambda command: "/usr/bin/lizard")

    def fake_run_or_raise(cmd, cwd, *, env=None):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})

    monkeypatch.setattr("raidar.commands.quality.run_or_raise", fake_run_or_raise)

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

    monkeypatch.setattr("raidar.commands.env_harbor.cleanup_stale_harbor_before_runs", lambda: None)
    monkeypatch.setattr(
        "raidar.commands.env_harbor.docker_compose_preflight_reason",
        lambda env: None,
    )

    def fake_run_or_raise(cmd, cwd, *, env=None):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})

    monkeypatch.setattr("raidar.commands.env_harbor.run_or_raise", fake_run_or_raise)
    monkeypatch.setattr(
        "raidar.commands.env_harbor.subprocess.run",
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

    monkeypatch.setattr(
        "raidar.commands.run_experiment.execute_run_options",
        fake_execute_run_options,
    )

    result = runner.invoke(
        main,
        [
            "experiment",
            "run",
            "--scenario",
            str(scenario_path),
            "--harness",
            "codex-cli",
            "--provider",
            "openai",
            "--model",
            "gpt-5.4",
            "--reasoning-effort",
            "high",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["force_experiment_summary"] is True
    assert captured["cleanup_before_runs"] is True
    assert captured["echo"] is True
    assert captured["execution_suffix"] == "codex-cli__openai-gpt-5.4__high"
    assert captured["options"].repeats == 5


def test_experiment_run_routes_research_loop_kind(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text("name: placeholder\n")
    captured: dict[str, object] = {}

    def fake_execute_run_options(options, **kwargs):
        captured["options"] = options
        captured.update(kwargs)

    monkeypatch.setattr(
        "raidar.commands.run_experiment.execute_run_options",
        fake_execute_run_options,
    )

    result = runner.invoke(
        main,
        [
            "experiment",
            "run",
            "--scenario",
            str(scenario_path),
            "--harness",
            "codex-cli",
            "--provider",
            "openai",
            "--model",
            "gpt-5.4",
            "--reasoning-effort",
            "high",
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

    run = _sample_machine_readable_run(tmp_path)

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

    monkeypatch.setattr(
        "raidar.commands.run_experiment.execute_run_options",
        fake_execute_run_options,
    )

    result = _invoke_experiment_run_json(runner, scenario_path)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary_path"] == str(tmp_path / "experiment-summary.json")
    assert payload["retries_used"] == 1
    assert payload["runs"][0]["run_json_path"] == str(tmp_path / "runs" / "run-01" / "run.json")


def test_matrix_dry_run_uses_config_scenario_entries(tmp_path: Path) -> None:
    runner = CliRunner()
    scenario_root = tmp_path / "scenarios" / "hello-world-smoke"
    revision_dir = scenario_root / "v001"
    revision_dir.mkdir(parents=True)
    (revision_dir / "scenario.yaml").write_text(
        "\n".join(
            [
                "name: hello-world-smoke",
                "scenario_revision: v001",
                "parent_revision: null",
                "description: Smoke scenario",
                "difficulty: easy",
                "category: smoke",
                "timeout_sec: 300",
                "starter:",
                "  root: starter",
                "verification:",
                "  gates: []",
                "  required_commands: []",
                "  min_quality_score: 0.0",
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
    config_path = tmp_path / "matrix.yaml"
    config_path.write_text(
        "\n".join(
            [
                "matrix:",
                "  id: smoke-test",
                f"  scenario: {scenario_root}",
                "  experiment:",
                "    timeout_sec: 1800",
                "    repeats: 2",
                "    repeat_parallel: 1",
                "    retry_void: 0",
                "  entries:",
                "    - id: gemini-v001",
                "      scenario_revision: v001",
                "      agent:",
                "        harness: gemini",
                "        provider: google",
                "        model: gemini-3-flash-preview",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        main,
        [
            "matrix",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"Loading matrix from {config_path}" in result.output
    assert "Matrix 'smoke-test' defined for 1 experiments" in result.output
    assert (
        "[dry-run] gemini-v001: hello-world-smoke@v001: "
        "gemini/google/gemini-3-flash-preview x2" in result.output
    )


def test_matrix_requires_config() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["matrix", "--dry-run"])

    assert result.exit_code != 0
    assert "Missing option '--config'" in result.output


def test_matrix_rejects_old_agents_shape(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "matrix.yaml"
    config_path.write_text(
        "\n".join(
            [
                "matrix:",
                "  id: legacy",
                "  scenario: scenarios/hello-world-smoke",
                "  experiment:",
                "    timeout_sec: 1800",
                "    repeats: 5",
                "    repeat_parallel: 1",
                "    retry_void: 0",
                "  agents:",
                "    - harness: codex-cli",
                "      provider: openai",
                "      model: gpt-5.4",
            ]
        )
        + "\n"
    )

    result = runner.invoke(
        main,
        [
            "matrix",
            "--config",
            str(config_path),
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    assert "entries" in result.output


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
    assert scenario_def.parent_revision is None
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
        "code-quality",
        "test-coverage",
        "artifact-checks",
        "verification-stability",
        "resource-efficiency",
    ]

    validate_result = runner.invoke(
        main, ["scenario", "validate", "--scenario", str(scenario_yaml)]
    )
    assert validate_result.exit_code == 0, validate_result.output
    assert "parent_revision: None" in validate_result.output

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
    assert payload["parent_revision"] is None
    assert Path(payload["scenario_yaml"]).is_file()
    assert Path(payload["prompt_path"]).is_file()
    assert Path(payload["rules_dir"]).is_dir()


def test_artifact_guard_allows_generated_artifact_deletions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "raidar.application.repo_state.changed_repo_entries",
        lambda _: [
            (
                "D",
                "experiments/20260220-000000Z__hello-world-smoke__v001/runs/run-01/run.json",
            ),
        ],
    )

    assert_no_generated_artifact_changes(tmp_path)


def test_artifact_guard_rejects_modified_generated_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "raidar.application.repo_state.changed_repo_entries",
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
        assert_no_generated_artifact_changes(tmp_path)
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
    assert "parent_revision: v001" in clone_result.output

    cloned_scenario_yaml = scenario_dir / "v002" / "scenario.yaml"
    cloned_scenario = ScenarioDefinition.from_yaml(cloned_scenario_yaml)
    assert cloned_scenario.scenario_revision == "v002"
    assert cloned_scenario.parent_revision == "v001"
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


def test_scenario_clone_revision_skips_ignored_generated_artifacts_in_git_repo(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    scenario_dir = tmp_path / "scenarios" / "sample-task"

    (tmp_path / ".gitignore").write_text(
        ".DS_Store\n.next/\ncoverage/\nnode_modules/\n*.tsbuildinfo\n"
    )
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)

    init_result = runner.invoke(
        main,
        ["scenario", "init", "--path", str(scenario_dir), "--name", "sample-task"],
    )
    assert init_result.exit_code == 0, init_result.output

    _create_starter_files(scenario_dir, revision="v001")
    starter_dir = scenario_dir / "v001" / "starter"
    (starter_dir / "tsconfig.tsbuildinfo").write_text("{}\n")
    (starter_dir / ".next" / "cache").mkdir(parents=True, exist_ok=True)
    (starter_dir / ".next" / "cache" / "build.txt").write_text("generated\n")
    (starter_dir / "node_modules" / "dep").mkdir(parents=True, exist_ok=True)
    (starter_dir / "node_modules" / "dep" / "index.js").write_text("export {};\n")
    (starter_dir / "coverage").mkdir(parents=True, exist_ok=True)
    (starter_dir / "coverage" / "summary.json").write_text("{}\n")
    (scenario_dir / "v001" / ".DS_Store").write_text("junk\n")

    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-f", str(starter_dir / "tsconfig.tsbuildinfo")],
        check=True,
        capture_output=True,
    )

    clone_result = runner.invoke(
        main,
        ["scenario", "clone-revision", "--path", str(scenario_dir), "--from-revision", "v001"],
    )
    assert clone_result.exit_code == 0, clone_result.output

    cloned_starter = scenario_dir / "v002" / "starter"
    assert (cloned_starter / "src" / "app" / "page.tsx").exists()
    assert (cloned_starter / "tsconfig.tsbuildinfo").exists()
    assert not (cloned_starter / ".next").exists()
    assert not (cloned_starter / "node_modules").exists()
    assert not (cloned_starter / "coverage").exists()
    assert not (scenario_dir / "v002" / ".DS_Store").exists()


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
    non_numeric_revision = scenario_dir / "vx" / "scenario.yaml"
    non_numeric_revision.parent.mkdir(parents=True)
    non_numeric_revision.write_text("name: sample-task\nscenario_revision: vx\n", encoding="utf-8")

    info_result = runner.invoke(main, ["info", "--scenario", str(scenario_dir)])
    assert info_result.exit_code == 0, info_result.output
    assert "Revision: v10" in info_result.output
    assert "Parent Revision: None" in info_result.output
    assert f"Scenario YAML: {scenario_dir / 'v10' / 'scenario.yaml'}" in info_result.output
    assert "Available Revisions:" in info_result.output
    assert f"  vx: {scenario_dir / 'vx' / 'scenario.yaml'}" in info_result.output
    assert f"  v2: {scenario_dir / 'v2' / 'scenario.yaml'}" in info_result.output
    assert f"  v10: {scenario_dir / 'v10' / 'scenario.yaml'}" in info_result.output
    assert (
        "Evaluation Profile: "
        "scorers:typescript-code-task@1:0.9+resource-efficiency@1:0.1" in info_result.output
    )


def _write_experiment_summary(fixture: ExperimentSummaryFixture) -> None:
    fixture.execution_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at_utc": fixture.created_at,
        "config": {
            "scenario_name": fixture.scenario_name,
            "harness": fixture.harness,
            "model": fixture.model,
            "evaluation_profile": fixture.evaluation_profile,
        },
        "aggregate": {
            "run_count_total": fixture.run_count_total,
            "unscored_count": fixture.unscored_count,
        },
    }
    (fixture.execution_dir / "experiment-summary.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _seed_experiment_list_summaries(experiments_root: Path) -> None:
    _write_experiment_summary(
        ExperimentSummaryFixture(
            experiments_root / "20260222-100000Z__hello-world-smoke__v001",
            scenario_name="hello-world-smoke",
            model="anthropic/claude-haiku-4-5",
            harness="claude-code",
            created_at="2026-02-22T10:00:00+00:00",
        )
    )
    _write_experiment_summary(
        ExperimentSummaryFixture(
            experiments_root / "20260222-110000Z__homepage-implementation__v001",
            scenario_name="homepage-implementation",
            model="codex/gpt-5.4-high",
            harness="codex-cli",
            evaluation_profile=f"{DEFAULT_EXPERIMENT_PROFILE}+visual-regression",
            created_at="2026-02-22T11:00:00+00:00",
        )
    )


def _invoke_experiments_list(runner: CliRunner, experiments_root: Path, *args: str):
    return runner.invoke(
        main,
        ["experiments", "list", "--experiments-root", str(experiments_root), *args],
    )


def _assert_filtered_experiment_text(output: str) -> None:
    assert "homepage-implementation@v001" in output
    assert "hello-world-smoke@v001" not in output
    assert (
        "evaluation_profile=functional+requirements-coverage+verification-stability+execution-validity+resource-efficiency+visual-regression"
        in output
    )


def _seed_prune_summaries(experiments_root: Path) -> PruneExperimentDirs:
    dirs = PruneExperimentDirs(
        old_dir=experiments_root / "20260220-100000Z__hello-world-smoke__v001",
        new_dir=experiments_root / "20260221-100000Z__hello-world-smoke__v001",
        other_model_dir=experiments_root / "20260222-100000Z__hello-world-smoke__v001",
    )
    _write_experiment_summary(
        _summary_fixture(dirs.old_dir, "anthropic/claude-haiku-4-5", "2026-02-20T10:00:00+00:00")
    )
    _write_experiment_summary(
        _summary_fixture(dirs.new_dir, "anthropic/claude-haiku-4-5", "2026-02-21T10:00:00+00:00")
    )
    _write_experiment_summary(
        _summary_fixture(dirs.other_model_dir, "codex/gpt-5.4-high", "2026-02-22T10:00:00+00:00")
    )
    return dirs


def _summary_fixture(execution_dir: Path, model: str, created_at: str):
    harness = "codex-cli" if model.startswith("codex/") else "claude-code"
    return ExperimentSummaryFixture(
        execution_dir,
        scenario_name="hello-world-smoke",
        model=model,
        harness=harness,
        created_at=created_at,
    )


def test_experiments_list_filters_and_json_output(tmp_path: Path) -> None:
    runner = CliRunner()
    experiments_root = tmp_path / "experiments"
    _seed_experiment_list_summaries(experiments_root)

    text_result = _invoke_experiments_list(runner, experiments_root, "--scenario", "homepage")
    assert text_result.exit_code == 0, text_result.output
    _assert_filtered_experiment_text(text_result.output)

    profile_result = _invoke_experiments_list(
        runner, experiments_root, "--evaluation-profile", "visual-regression", "--json"
    )
    assert profile_result.exit_code == 0, profile_result.output
    profile_rows = json.loads(profile_result.output)
    assert len(profile_rows) == 1
    assert profile_rows[0]["scenario_name"] == "homepage-implementation"

    json_result = _invoke_experiments_list(runner, experiments_root, "--json")
    assert json_result.exit_code == 0, json_result.output
    rows = json.loads(json_result.output)
    assert isinstance(rows, list)
    assert rows[0]["execution_id"] == "20260222-110000Z__homepage-implementation__v001"
    assert rows[0]["evaluation_profile"] == (
        "functional+requirements-coverage+verification-stability+execution-validity+resource-efficiency+visual-regression"
    )


def test_experiments_prune_keeps_latest_per_model(tmp_path: Path) -> None:
    runner = CliRunner()
    experiments_root = tmp_path / "experiments"
    archive_root = tmp_path / "archive"
    dirs = _seed_prune_summaries(experiments_root)

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
    assert dirs.new_dir.exists()
    assert dirs.other_model_dir.exists()
    assert not dirs.old_dir.exists()
    assert _archive_destination(dirs.old_dir, archive_root).exists()
    assert "experiments_pruned=1" in result.output


def test_experiments_prune_dry_run_does_not_move_directories(tmp_path: Path) -> None:
    runner = CliRunner()
    experiments_root = tmp_path / "experiments"
    archive_root = tmp_path / "archive"
    old_dir = experiments_root / "20260220-100000Z__hello-world-smoke__v001"
    new_dir = experiments_root / "20260221-100000Z__hello-world-smoke__v001"
    _write_experiment_summary(
        ExperimentSummaryFixture(
            old_dir,
            scenario_name="hello-world-smoke",
            model="anthropic/claude-haiku-4-5",
            harness="claude-code",
            created_at="2026-02-20T10:00:00+00:00",
        )
    )
    _write_experiment_summary(
        ExperimentSummaryFixture(
            new_dir,
            scenario_name="hello-world-smoke",
            model="anthropic/claude-haiku-4-5",
            harness="claude-code",
            created_at="2026-02-21T10:00:00+00:00",
        )
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


def test_execute_run_command_passes_reruns_used(monkeypatch, tmp_path: Path) -> None:
    scenario = _minimal_cli_scenario()
    options = _run_options(tmp_path)
    request = type("Request", (), {"scenario": scenario})()
    run = _minimal_eval_run()

    captured: dict[str, object] = {}

    def fake_create_experiment_summary(summary_input):
        captured["summary_input"] = summary_input
        return {"experiment_id": "exp-01"}

    monkeypatch.setattr(execution, "_load_project_env", lambda _repo_root: None)
    monkeypatch.setattr(execution, "_cleanup_stale_harbor_before_runs", lambda: None)
    monkeypatch.setattr(
        execution,
        "prepared_run_request",
        lambda *_args, **_kwargs: (
            scenario,
            datetime(2026, 3, 10, 13, 0, 0, tzinfo=UTC),
            tmp_path / "experiments" / "exp-01",
            request,
        ),
    )
    monkeypatch.setattr(
        execution,
        "execute_repeat_runs",
        lambda **_kwargs: ([run], 1, 0),
    )
    monkeypatch.setattr(execution, "scenario_evaluation_profile", lambda _scenario: "functional")
    monkeypatch.setattr(execution, "scenario_metrics", lambda _scenario: ["functional"])
    monkeypatch.setattr(execution, "create_experiment_summary", fake_create_experiment_summary)
    monkeypatch.setattr(
        execution,
        "persist_experiment",
        lambda *_args, **_kwargs: (
            tmp_path / "experiment.json",
            tmp_path / "experiment-summary.json",
            tmp_path / "report.md",
        ),
    )

    _dispatch_execute_run_command(options, tmp_path)

    summary_input = captured["summary_input"]
    assert summary_input.reruns_used == 1
    assert not hasattr(summary_input, "retries_used")


def test_run_agent_smoke_script_uses_make_targets(tmp_path: Path) -> None:
    fake_env = _fake_make_script_env(tmp_path)
    script_path = fake_env.repo_root / "scripts" / "checks" / "run-agent-smoke.sh"

    result = _run_with_fake_commands(
        fake_env,
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
        ],
    )

    assert result.returncode == 0, result.stderr
    assert fake_env.make_log.read_text(encoding="utf-8").splitlines() == [
        (
            f"ARGS:-C {fake_env.repo_root} agent-smoke HARNESS=codex-cli "
            "PROVIDER=openai MODEL=gpt-5.5 TIMEOUT_SEC=120 "
            "AGENT_SMOKE_SCENARIO=scenarios/hello-world-smoke/v001/scenario.yaml "
            "AGENT_SMOKE_REPEATS=2 AGENT_SMOKE_REPEAT_PARALLEL=3 "
            "AGENT_SMOKE_RERUN_UNSCORED=1"
        ),
        "ENV::",
    ]


def test_orchestrator_smoke_make_target_supports_repeat_overrides(tmp_path: Path) -> None:
    fake_env = _fake_command_env(tmp_path)

    result = _run_with_fake_commands(
        fake_env,
        [
            "make",
            "orchestrator-smoke",
            "ORCHESTRATOR_SMOKE_REPEATS=2",
            "RUN_PARALLELISM=2",
        ],
    )

    assert result.returncode == 0, result.stderr
    assert fake_env.make_log.read_text(encoding="utf-8").splitlines() == [
        "DOCKER:info",
        (
            "UV:run --project orchestrator raidar run "
            "--scenario scenarios/hello-world-smoke/v001/scenario.yaml "
            "--harness codex-cli --provider openai --model gpt-5.5 --reasoning-effort low "
            "--repeats 2 --repeat-parallel 2 --rerun-unscored 0 "
            "--experiment-kind benchmark"
        ),
        "ENV::",
    ]


def test_smoke_matrix_make_target_uses_default_smoke_scenario(tmp_path: Path) -> None:
    fake_env = _fake_command_env(tmp_path)

    result = _run_with_fake_commands(
        fake_env,
        ["make", "smoke-matrix"],
    )

    assert result.returncode == 0, result.stderr
    assert fake_env.make_log.read_text(encoding="utf-8").splitlines() == [
        "DOCKER:info",
        (
            "UV:run --project orchestrator raidar matrix "
            "--config matrices/hello-world-smoke-trio.yaml "
            "--experiment-kind benchmark"
        ),
        "ENV::",
    ]


def test_agent_smoke_make_target_uses_canonical_routing(tmp_path: Path) -> None:
    fake_env = _fake_command_env(tmp_path)

    result = _run_with_fake_commands(
        fake_env,
        [
            "make",
            "agent-smoke",
            "HARNESS=gemini",
            "PROVIDER=google",
            "MODEL=gemini-3-flash-preview",
        ],
    )

    assert result.returncode == 0, result.stderr
    assert fake_env.make_log.read_text(encoding="utf-8").splitlines() == [
        "DOCKER:info",
        (
            "UV:run --project orchestrator raidar experiment run "
            "--scenario scenarios/hello-world-smoke/v001/scenario.yaml "
            "--harness gemini --provider google --model gemini-3-flash-preview "
            "--repeats 1 --repeat-parallel 1 --rerun-unscored 0 "
            "--experiment-kind benchmark"
        ),
        "ENV::",
    ]


def test_agent_smoke_make_target_defaults_codex_to_chatgpt_auth(tmp_path: Path) -> None:
    fake_env = _fake_command_env(tmp_path, codex_auth_mode=True)

    result = _run_with_fake_commands(
        fake_env,
        [
            "make",
            "agent-smoke",
            "HARNESS=codex-cli",
            "PROVIDER=openai",
            "MODEL=gpt-5.4-mini",
            "AGENT_SMOKE_REASONING_EFFORT=low",
        ],
    )

    assert result.returncode == 0, result.stderr
    assert fake_env.make_log.read_text(encoding="utf-8").splitlines() == [
        "DOCKER:info",
        (
            "UV:run --project orchestrator raidar experiment run "
            "--scenario scenarios/hello-world-smoke/v001/scenario.yaml "
            "--harness codex-cli --provider openai --model gpt-5.4-mini --reasoning-effort low "
            "--repeats 1 --repeat-parallel 1 --rerun-unscored 0 "
            "--experiment-kind benchmark"
        ),
        "ENV:::chatgpt",
    ]


def test_agent_smoke_make_target_reports_pre_experiment_boundary_time(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        "\n".join(
            [
                "#!/bin/sh",
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
    fake_uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    result = subprocess.run(
        [
            "make",
            "agent-smoke",
            "HARNESS=codex-cli",
            "PROVIDER=openai",
            "MODEL=gpt-5.5",
            "AGENT_SMOKE_REASONING_EFFORT=low",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "pre_experiment_sec=" in result.stdout
    assert "(boundary=before experiment-run)" in result.stdout


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
