"""CLI utility commands, quality gates and the public smoke make targets."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import env_harbor, main
from raidar.contract.paths import ORCHESTRATOR_ROOT, REPO_ROOT
from raidar.subject.agent.config import agent_name_for
from raidar.subject.environment.python.pack_definition import load_pack_spec

quality_gates = main.commands["quality"].commands["gates"]


def test_cli_version_matches_pyproject_version() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])

    assert result.exit_code == 0, result.output
    pyproject_data = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    expected_version = pyproject_data["project"]["version"]
    assert result.output.strip().endswith(expected_version)


def test_agent_list_names_harbor_agents_with_model_variations() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["agent", "list"])

    assert result.exit_code == 0, result.output
    assert "claude-code  -> CLAUDE.md  (CLI claude-code)" in result.output
    assert "codex        -> AGENTS.md  (CLI codex-cli)" in result.output
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


def test_agent_validate_reports_codex_auth_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    monkeypatch.setenv("CODEX_CLI_PATH", "/usr/local/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))

    result = runner.invoke(
        main,
        [
            "agent",
            "validate",
            "--agent",
            "codex",
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


def test_agent_setup_auth_is_noop_when_auth_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    (auth_dir / "auth.json").write_text('{"access_token":"token"}', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(auth_dir))

    result = runner.invoke(main, ["agent", "setup-auth", "--agent", "codex"])

    assert result.exit_code == 0, result.output
    assert "Codex auth is already configured." in result.output
    assert f"auth_json_path: {auth_dir / 'auth.json'}" in result.output


def test_agent_setup_auth_runs_codex_login_with_device_auth(
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

    monkeypatch.setattr("raidar.cli.agent.subprocess.run", fake_run)

    result = runner.invoke(
        main,
        ["agent", "setup-auth", "--agent", "codex", "--device-auth"],
    )

    assert result.exit_code == 0, result.output
    assert commands == [["/usr/local/bin/codex", "login", "--device-auth"]]
    assert "Codex auth setup complete." in result.output


def test_agent_setup_auth_fails_when_login_does_not_create_auth_json(
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

    monkeypatch.setattr("raidar.cli.agent.subprocess.run", fake_run)

    result = runner.invoke(main, ["agent", "setup-auth", "--agent", "codex"])

    assert result.exit_code != 0
    assert "no file-backed auth.json was found" in result.output


def test_quality_gates_writes_coverage_to_pytest_cache(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "raidar.cli.quality.repo_state.assert_no_generated_artifact_changes",
        lambda repo_root: None,
    )
    monkeypatch.setattr(
        "raidar.cli.quality.repo_state.has_unstaged_changes", lambda repo_root: False
    )
    monkeypatch.setattr("raidar.cli.quality.shutil.which", lambda command: "/usr/bin/lizard")

    def fake_run_or_raise(cmd, cwd, *, env=None):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})

    monkeypatch.setattr("raidar.cli.quality.run_or_raise", fake_run_or_raise)

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

    monkeypatch.setattr("raidar.cli.env_harbor.compose_preflight_reason", lambda: None)

    def fake_run_or_raise(cmd, cwd, *, env=None):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env})

    monkeypatch.setattr("raidar.cli.env_harbor.run_or_raise", fake_run_or_raise)
    monkeypatch.setattr(
        "raidar.cli.env_harbor.subprocess.run",
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
    # Harbor comes from the exact-pinned project dependency, not a global uv tool.
    assert len(calls) == 2
    assert "harbor 0.0.0" in result.output


def test_env_setup_refuses_an_old_docker_compose(monkeypatch) -> None:
    monkeypatch.setattr(
        "raidar.cli.env_harbor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="2.30.0\n"),
    )

    result = CliRunner().invoke(main, ["env", "setup", "--no-install-tools"])

    assert result.exit_code != 0
    assert "Unsupported docker compose version 2.30.0. Require >= 2.40.1" in result.output


def test_compose_preflight_accepts_new_unknown_or_missing_compose(monkeypatch) -> None:
    outputs = iter(["v2.40.1\n", "not a version\n"])
    monkeypatch.setattr(
        "raidar.cli.env_harbor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=next(outputs)),
    )
    assert env_harbor.compose_preflight_reason() is None
    assert env_harbor.compose_preflight_reason() is None

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr("raidar.cli.env_harbor.subprocess.run", missing)
    assert env_harbor.compose_preflight_reason() is None


def test_harbor_cleanup_reports_removed_trial_environments(
    monkeypatch, tmp_path: Path, docker_target_offline: None
) -> None:
    seen: dict[str, object] = {}

    def fake_cleanup(job_dir, *, docker_context, dry_run):
        seen.update(job_dir=job_dir, docker_context=docker_context, dry_run=dry_run)
        return ["b00-p0-baseline__env-0123456789"]

    monkeypatch.setattr("raidar.cli.env_harbor.cleanup_trial_projects", fake_cleanup)

    result = CliRunner().invoke(main, ["harbor", "cleanup", "--job", str(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert seen == {"job_dir": tmp_path, "docker_context": "test-ctx", "dry_run": True}
    assert "would remove: b00-p0-baseline__env-0123456789" in result.output
    assert "1 trial environment(s) would remove" in result.output


def test_harbor_cleanup_surfaces_docker_failures(monkeypatch, docker_target_offline: None) -> None:
    def failing(job_dir, *, docker_context, dry_run):
        raise RuntimeError("docker ps failed: no daemon")

    monkeypatch.setattr("raidar.cli.env_harbor.cleanup_trial_projects", failing)

    result = CliRunner().invoke(main, ["harbor", "cleanup"])

    assert result.exit_code != 0
    assert "docker ps failed: no daemon" in result.output


def test_cli_exposes_only_the_experiment_model_commands() -> None:
    assert set(main.commands) == {
        "agent",
        "analyze",
        "assess",
        "env",
        "harbor",
        "health",
        "init",
        "jev",
        "ledger",
        "lock",
        "pack",
        "perf",
        "plan",
        "problem",
        "quality",
        "run",
        "target",
        "validate",
    }
    problem = main.commands["problem"]
    assert isinstance(problem, click.Group)
    assert set(problem.commands) == {"errata-draft", "import", "info", "list", "validate"}


def test_run_refuses_the_removed_scenario_flag(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["run", "--scenario", str(tmp_path)])

    assert result.exit_code != 0
    assert "No such option: --scenario" in result.output


SMOKE_PATH = "lab/experiments/smoke/python__circuit_eval-cp1__agent-health/experiment.yaml"
AGENTS_PATH = "lab/experiments/benchmark/python__circuit_eval-cp1__agents/experiment.yaml"


def _make_dry_run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", "-n", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _raidar_lines(output: str, command: str) -> list[str]:
    """Each ``raidar <command>`` invocation's arguments, continuation lines joined."""
    marker = f"raidar {command} "
    joined = re.sub(r"\\\n\s*", " ", output)
    return [line.split(marker, 1)[1] for line in joined.splitlines() if marker in line]


def test_orchestrator_smoke_locks_and_runs_the_smoke_experiment_with_the_same_overrides() -> None:
    result = _make_dry_run("orchestrator-smoke", "SMOKE_ATTEMPTS=2", "SMOKE_CONCURRENCY=2")

    assert result.returncode == 0, result.stderr
    lock_args = _raidar_lines(result.stdout, "lock")
    run_args = _raidar_lines(result.stdout, "run")
    assert len(lock_args) == len(run_args) == 1
    assert lock_args[0].startswith(f'"{SMOKE_PATH}" --output ')
    assert '--set "analysis.attempts=2"' in lock_args[0]
    assert run_args[0].startswith(f'"{SMOKE_PATH}" --lock ')
    assert '--set "analysis.attempts=2"' in run_args[0]
    assert '--concurrency "2"' in run_args[0]
    assert "orchestrator-smoke.lock.json" in lock_args[0]
    assert "orchestrator-smoke.lock.json" in run_args[0]


def test_agent_smoke_replaces_the_agent_and_pins_the_pack_version() -> None:
    # AGENT=claude-code alone defaults to claude-haiku-4-5 with the effort unset
    # (an empty --set value unsets the field; haiku has no effort levels).
    result = _make_dry_run("agent-smoke", "AGENT=claude-code", "TIMEOUT_SEC=300")

    assert result.returncode == 0, result.stderr
    (run_args,) = _raidar_lines(result.stdout, "run")
    for assignment in (
        "agents.0.name=claude-code",
        "agents.0.provider=anthropic",
        "agents.0.model=claude-haiku-4-5",
        "agents.0.reasoning_effort=",
        "agents.0.version='2.1.285'",
        "protocol.agent_timeout_sec=300",
    ):
        assert f'--set "{assignment}"' in run_args


@pytest.mark.parametrize(
    ("args", "effort"),
    [
        ((), "low"),
        (("REASONING_EFFORT=",), ""),
        (("AGENT=claude-code", "REASONING_EFFORT=low"), "low"),
    ],
)
def test_agent_smoke_effort_defaults_per_agent_and_empty_means_unset(
    args: tuple[str, ...], effort: str
) -> None:
    result = _make_dry_run("agent-smoke", *args)

    assert result.returncode == 0, result.stderr
    (run_args,) = _raidar_lines(result.stdout, "run")
    assert f'--set "agents.0.reasoning_effort={effort}"' in run_args


@pytest.mark.parametrize("arm", ["codex", "claude"])
def test_smoke_agents_arm_runs_one_arm_of_the_agents_smoke(arm: str) -> None:
    result = _make_dry_run("smoke-agents", f"SMOKE_ARM={arm}")

    assert result.returncode == 0, result.stderr
    (run_args,) = _raidar_lines(result.stdout, "run")
    assert run_args.startswith(f'"{AGENTS_PATH}"')
    assert f'--arm "{arm}"' in run_args


def test_smoke_agents_arms_match_the_agents_smoke_experiment() -> None:
    agents_smoke = REPO_ROOT / AGENTS_PATH
    spec = yaml.safe_load(agents_smoke.read_text(encoding="utf-8"))
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    arms = " ".join(arm["id"] for arm in spec["arms"])

    assert f"SMOKE_ARMS := {arms}\n" in makefile


@pytest.mark.parametrize("arm", ["gemini", "invalid"])
def test_smoke_agents_rejects_an_arm_the_pack_cannot_run(arm: str) -> None:
    result = _make_dry_run("smoke-agents", f"SMOKE_ARM={arm}")

    assert result.returncode != 0
    assert f"Unknown SMOKE_ARM '{arm}'; expected one of: codex claude" in result.stderr


def test_smoke_dry_run_check_prints_every_public_smoke_shape() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "smoke-dry-run-check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    run_lines = _raidar_lines(result.stdout, "run")
    assert len(run_lines) == 5
    assert sum('--arm "' in line for line in run_lines) == 2


@pytest.mark.parametrize(
    ("args", "command", "expected"),
    [
        (("problem-list",), "problem", ["list"]),
        (
            ("problem-info", "PROBLEM=scb/forge@38d627ecf668"),
            "problem",
            ["info", '"scb/forge@38d627ecf668"'],
        ),
        (
            ("experiment-init", "EXPERIMENT=my-exp/experiment.yaml", "EXPERIMENT_ID=my-exp"),
            "init",
            ['"my-exp/experiment.yaml"', "--id", '"my-exp"'],
        ),
    ],
)
def test_registry_and_init_targets_call_their_raidar_verbs(
    args: tuple[str, ...], command: str, expected: list[str]
) -> None:
    result = _make_dry_run(*args)

    assert result.returncode == 0, result.stderr
    (arguments,) = _raidar_lines(result.stdout, command)
    assert arguments.split() == expected


@pytest.mark.parametrize(
    ("target", "variable"), [("problem-info", "PROBLEM"), ("experiment-init", "EXPERIMENT")]
)
def test_registry_and_init_targets_require_their_argument(target: str, variable: str) -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", target],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert f"Missing required variable: {variable}" in result.stdout


def test_review_fixture_is_written_under_the_fixture_dir() -> None:
    result = _make_dry_run("review-fixture-synthetic")

    assert result.returncode == 0, result.stderr
    assert "tests.review_fixtures.projection" in result.stdout
    assert "/var/tmp/review-fixture" in result.stdout
    assert "experiments/" not in result.stdout


def test_review_surface_test_projects_both_fixture_roots_but_quality_only_one() -> None:
    result = _make_dry_run("review-surface-test")

    assert result.returncode == 0, result.stderr
    joined = re.sub(r"\\\n\s*", " ", result.stdout)
    review = next(
        line for line in joined.splitlines() if "raidar.evaluation.review.projection " in line
    )
    quality = next(
        line for line in joined.splitlines() if "raidar.evaluation.review.quality " in line
    )
    assert "--legacy-root" not in review
    assert "/var/tmp/review-fixture/review-only" in review
    assert "/var/tmp/review-fixture/jobs" in review
    assert "/var/tmp/review-fixture/review-only" not in quality


def test_review_surface_data_requires_jobs() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "review-surface-data", "REVIEW_JOBS_ROOT="],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Missing required variable: REVIEW_JOBS_ROOT" in result.stdout


def test_review_surface_test_applies_one_store_to_every_given_runs_root() -> None:
    result = _make_dry_run(
        "review-surface-test", "REVIEW_JOBS_ROOT=runs/a runs/b", "REVIEW_STORE=store"
    )

    assert result.returncode == 0, result.stderr
    joined = re.sub(r"\\\n\s*", " ", result.stdout)
    quality = next(
        line for line in joined.splitlines() if "raidar.evaluation.review.quality " in line
    )
    assert f'--jobs-root "{REPO_ROOT / "runs/a"}"' in quality
    assert f'--jobs-root "{REPO_ROOT / "runs/b"}"' in quality
    assert quality.count(f'--store "{REPO_ROOT / "store"}"') == 2


def test_review_surface_test_rejects_mismatched_runs_and_stores() -> None:
    result = _make_dry_run(
        "review-surface-test", "REVIEW_JOBS_ROOT=runs/a runs/b runs/c", "REVIEW_STORE=s1 s2"
    )

    assert result.returncode != 0
    assert "one store for all roots or one store per root" in result.stderr


def test_smoke_experiments_pin_the_pack_agent_versions() -> None:
    pins = {
        agent_name_for(cli).value: pin.version
        for cli, pin in load_pack_spec().image.harnesses.items()
    }
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    for name, version in pins.items():
        assert f"AGENT_SMOKE_VERSION_{name} := {version}" in makefile
    for path in (SMOKE_PATH, AGENTS_PATH):
        spec = yaml.safe_load((REPO_ROOT / path).read_text())
        for agent in spec["agents"]:
            assert agent["version"] == pins[agent["name"]]


@pytest.mark.parametrize(
    ("old", "new"),
    [("RUN=x", "JOB="), ("HARNESS=codex", "AGENT="), ("BLOCK=0", "ATTEMPT="), ("RUNS=x", "JOBS=")],
)
def test_renamed_make_variables_fail_with_their_new_name(old: str, new: str) -> None:
    result = _make_dry_run("help", old)

    assert result.returncode != 0
    assert f"was renamed {new}" in result.stderr


def test_renamed_make_variables_fail_from_the_environment_too() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "RUNS_DIR": "x"},
    )

    assert result.returncode != 0
    assert "RUNS_DIR= was renamed JOBS_DIR=" in result.stderr


@pytest.mark.skipif(
    (REPO_ROOT / "orchestrator" / ".env").exists(), reason="a local orchestrator/.env exists"
)
def test_secrets_dotenv_without_an_env_file_fails_before_running_anything() -> None:
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "agent-validate",
            "SECRETS=dotenv",
            "AGENT=codex",
            "PROVIDER=openai",
            "MODEL=gpt-6-luna",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "SECRETS=dotenv but orchestrator/.env does not exist" in result.stderr
    assert "secrets: orchestrator/.env" not in result.stderr


def test_experiment_assess_rescores_the_run_trials_with_the_named_scorers(tmp_path: Path) -> None:
    (tmp_path / "trials").mkdir()
    result = _make_dry_run(
        "experiment-assess",
        f"JOB={tmp_path}",
        "STORE=.tmp/store",
        "SCORERS=correctness@1 code-quality@3",
        "REPLAY=1",
    )

    assert result.returncode == 0, result.stderr
    (assess_args,) = _raidar_lines(result.stdout, "assess")
    assert assess_args.startswith(f'--rescore "{tmp_path}/trials" --store ".tmp/store"')
    assert '--scorer "correctness@1" --scorer "code-quality@3"' in assess_args
    assert "--replay" in assess_args
