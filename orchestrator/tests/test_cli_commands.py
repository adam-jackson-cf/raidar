"""CLI utility commands, quality gates and the public smoke make targets."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner

from raidar.cli import main
from raidar.commands import env_harbor
from raidar.commands.repo_state import (
    assert_no_generated_artifact_changes,
    generated_artifact_paths,
)
from raidar.commands.shared import ORCHESTRATOR_ROOT, REPO_ROOT
from raidar.packs.python import load_pack_spec

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

    monkeypatch.setattr("raidar.commands.env_harbor.compose_preflight_reason", lambda: None)

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
    # Harbor comes from the exact-pinned project dependency, not a global uv tool.
    assert len(calls) == 2
    assert "harbor 0.0.0" in result.output


def test_artifact_guard_allows_generated_artifact_deletions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "raidar.commands.repo_state.changed_repo_entries",
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
        "raidar.commands.repo_state.changed_repo_entries",
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

    with pytest.raises(
        click.ClickException,
        match="Generated Harbor artifacts must not be committed",
    ):
        assert_no_generated_artifact_changes(tmp_path)


def test_env_setup_refuses_an_old_docker_compose(monkeypatch) -> None:
    monkeypatch.setattr(
        "raidar.commands.env_harbor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="2.30.0\n"),
    )

    result = CliRunner().invoke(main, ["env", "setup", "--no-install-tools"])

    assert result.exit_code != 0
    assert "Unsupported docker compose version 2.30.0. Require >= 2.40.1" in result.output


def test_compose_preflight_accepts_new_unknown_or_missing_compose(monkeypatch) -> None:
    outputs = iter(["v2.40.1\n", "not a version\n"])
    monkeypatch.setattr(
        "raidar.commands.env_harbor.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout=next(outputs)),
    )
    assert env_harbor.compose_preflight_reason() is None
    assert env_harbor.compose_preflight_reason() is None

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr("raidar.commands.env_harbor.subprocess.run", missing)
    assert env_harbor.compose_preflight_reason() is None


def test_harbor_cleanup_reports_removed_trial_environments(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_cleanup(run_dir, *, dry_run):
        seen.update(run_dir=run_dir, dry_run=dry_run)
        return ["b00-p0-baseline__env-0123456789"]

    monkeypatch.setattr("raidar.commands.env_harbor.cleanup_trial_projects", fake_cleanup)

    result = CliRunner().invoke(main, ["harbor", "cleanup", "--run", str(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert seen == {"run_dir": tmp_path, "dry_run": True}
    assert "would remove: b00-p0-baseline__env-0123456789" in result.output
    assert "1 trial environment(s) would remove" in result.output


def test_harbor_cleanup_surfaces_docker_failures(monkeypatch) -> None:
    def failing(run_dir, *, dry_run):
        raise RuntimeError("docker ps failed: no daemon")

    monkeypatch.setattr("raidar.commands.env_harbor.cleanup_trial_projects", failing)

    result = CliRunner().invoke(main, ["harbor", "cleanup"])

    assert result.exit_code != 0
    assert "docker ps failed: no daemon" in result.output


def test_cli_exposes_only_the_experiment_model_commands() -> None:
    assert set(main.commands) == {
        "analyze",
        "assess",
        "env",
        "harbor",
        "harness",
        "jev",
        "lock",
        "pack",
        "perf",
        "plan",
        "problem",
        "quality",
        "run",
        "validate",
    }


def test_run_refuses_the_removed_scenario_flag(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["run", "--scenario", str(tmp_path)])

    assert result.exit_code != 0
    assert "No such option: --scenario" in result.output


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
    result = _make_dry_run("orchestrator-smoke", "SMOKE_BLOCKS=2", "SMOKE_CONCURRENCY=2")

    assert result.returncode == 0, result.stderr
    lock_args = _raidar_lines(result.stdout, "lock")
    run_args = _raidar_lines(result.stdout, "run")
    assert len(lock_args) == len(run_args) == 1
    assert lock_args[0].startswith('"examples/smoke/experiment.yaml" --output ')
    assert '--set "analysis.blocks=2"' in lock_args[0]
    assert run_args[0].startswith('"examples/smoke/experiment.yaml" --lock ')
    assert '--set "analysis.blocks=2"' in run_args[0]
    assert '--concurrency "2"' in run_args[0]
    assert "orchestrator-smoke.lock.json" in lock_args[0]
    assert "orchestrator-smoke.lock.json" in run_args[0]


def test_agent_smoke_replaces_the_agent_and_pins_the_pack_version() -> None:
    result = _make_dry_run(
        "agent-smoke",
        "HARNESS=claude-code",
        "PROVIDER=anthropic",
        "MODEL=claude-sonnet-4-6",
        "TIMEOUT_SEC=300",
    )

    assert result.returncode == 0, result.stderr
    (run_args,) = _raidar_lines(result.stdout, "run")
    for assignment in (
        "agents.0.harness=claude-code",
        "agents.0.provider=anthropic",
        "agents.0.model=claude-sonnet-4-6",
        "agents.0.reasoning_effort=low",
        "agents.0.version='2.1.285'",
        "protocol.agent_timeout_sec=300",
    ):
        assert f'--set "{assignment}"' in run_args


@pytest.mark.parametrize("selector", ["codex", "claude"])
def test_smoke_matrix_selector_runs_one_arm_of_the_agent_smoke(selector: str) -> None:
    result = _make_dry_run("smoke-matrix", f"SMOKE_MATRIX_SELECTOR={selector}")

    assert result.returncode == 0, result.stderr
    (run_args,) = _raidar_lines(result.stdout, "run")
    assert run_args.startswith('"examples/smoke/agents.experiment.yaml"')
    assert f'--arm "{selector}"' in run_args


@pytest.mark.parametrize(
    ("selector", "message"),
    [
        ("gemini", "the Python pack bakes in only codex-cli and claude-code"),
        ("invalid", "Unknown SMOKE_MATRIX_SELECTOR 'invalid'"),
    ],
)
def test_smoke_matrix_selector_rejects_unavailable_harnesses(selector: str, message: str) -> None:
    result = _make_dry_run("smoke-matrix", f"SMOKE_MATRIX_SELECTOR={selector}")

    assert result.returncode != 0
    assert message in result.stderr


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
    "target", ["scenario-init", "scenario-info", "matrix-run", "experiment-run", "experiments-list"]
)
def test_removed_scenario_targets_refuse_with_a_pointer(target: str) -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", target],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "removed at the reboot cutover" in result.stderr
    assert "experiment.yaml" in result.stderr


def test_smoke_experiments_pin_the_pack_harness_versions() -> None:
    pins = {harness: pin.version for harness, pin in load_pack_spec().image.harnesses.items()}
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    for harness, version in pins.items():
        assert f"AGENT_SMOKE_VERSION_{harness} := {version}" in makefile
    for name in ("experiment.yaml", "agents.experiment.yaml"):
        spec = yaml.safe_load((REPO_ROOT / "examples" / "smoke" / name).read_text())
        for agent in spec["agents"]:
            assert agent["version"] == pins[agent["harness"]]


def test_experiment_assess_rescores_the_run_trials_with_the_named_scorers(tmp_path: Path) -> None:
    (tmp_path / "trials").mkdir()
    result = _make_dry_run(
        "experiment-assess",
        f"RUN={tmp_path}",
        "STORE=.tmp/store",
        "SCORERS=correctness@1 code-quality@1",
        "REPLAY=1",
    )

    assert result.returncode == 0, result.stderr
    (assess_args,) = _raidar_lines(result.stdout, "assess")
    assert assess_args.startswith(f'--rescore "{tmp_path}/trials" --store ".tmp/store"')
    assert '--scorer "correctness@1" --scorer "code-quality@1"' in assess_args
    assert "--replay" in assess_args
