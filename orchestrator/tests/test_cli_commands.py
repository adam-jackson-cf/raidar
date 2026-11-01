"""CLI utility commands."""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from raidar.cli import env_harbor, main, process
from raidar.contract import paths


def test_cli_version_matches_pyproject_version() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])

    assert result.exit_code == 0, result.output
    pyproject_data = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    expected_version = pyproject_data["project"]["version"]
    assert result.output.strip().endswith(expected_version)


def test_agent_validate_reports_codex_auth_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    monkeypatch.setenv("CODEX_CLI_PATH", sys.executable)
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
    monkeypatch.setenv("CODEX_CLI_PATH", sys.executable)

    def fake_run(command, check=False):
        del check
        auth_path.write_text('{"access_token":"token"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("raidar.cli.agent.subprocess.run", fake_run)

    result = runner.invoke(
        main,
        ["agent", "setup-auth", "--agent", "codex", "--device-auth"],
    )

    assert result.exit_code == 0, result.output
    assert "Codex auth setup complete." in result.output


def test_agent_setup_auth_fails_when_login_does_not_create_auth_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(auth_dir))
    monkeypatch.setenv("CODEX_CLI_PATH", sys.executable)

    def fake_run(command, check=False):
        del check
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("raidar.cli.agent.subprocess.run", fake_run)

    result = runner.invoke(main, ["agent", "setup-auth", "--agent", "codex"])

    assert result.exit_code != 0
    assert "no file-backed auth.json was found" in result.output


@pytest.mark.parametrize("action", ["validate", "setup-auth"])
def test_agent_commands_explain_an_unusable_cli_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, action: str
) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", str(tmp_path / "missing-codex"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    arguments = ["agent", action, "--agent", "codex"]
    if action == "validate":
        arguments.extend(["--provider", "openai", "--model", "gpt-6-luna"])

    result = CliRunner().invoke(main, arguments)

    assert result.exit_code == 1
    assert "Error: codex-cli CLI not found or not executable" in result.output
    assert "CODEX_CLI_PATH" in result.output
    assert "passed" not in result.output


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
    def fake_cleanup(job_dir, *, docker_context, dry_run):
        return ["b00-p0-baseline__env-0123456789"]

    monkeypatch.setattr("raidar.cli.env_harbor.cleanup_trial_projects", fake_cleanup)

    result = CliRunner().invoke(main, ["harbor", "cleanup", "--job", str(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "would remove: b00-p0-baseline__env-0123456789" in result.output
    assert "1 trial environment(s) would remove" in result.output


def test_harbor_cleanup_surfaces_docker_failures(monkeypatch, docker_target_offline: None) -> None:
    def failing(job_dir, *, docker_context, dry_run):
        raise RuntimeError("docker ps failed: no daemon")

    monkeypatch.setattr("raidar.cli.env_harbor.cleanup_trial_projects", failing)

    result = CliRunner().invoke(main, ["harbor", "cleanup"])

    assert result.exit_code != 0
    assert "docker ps failed: no daemon" in result.output


@pytest.mark.parametrize(
    ("source", "existing", "expected"),
    [
        ("dotenv", None, "local"),
        ("dotenv", "shell", "shell"),
        ("infisical", None, None),
    ],
)
def test_cli_local_configuration_never_overrides_shell_or_infisical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    existing: str | None,
    expected: str | None,
) -> None:
    (tmp_path / ".env").write_text("RAIDAR_TEST_CONFIG=local\n")
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setenv("RAIDAR_SECRETS_SOURCE", source)
    monkeypatch.setenv("RAIDAR_TEST_CONFIG", "sentinel")
    monkeypatch.delenv("RAIDAR_TEST_CONFIG", raising=False)
    if existing is not None:
        monkeypatch.setenv("RAIDAR_TEST_CONFIG", existing)

    runpy.run_path(str(Path(process.__file__).with_name("__init__.py")))

    assert os.environ.get("RAIDAR_TEST_CONFIG") == expected


def test_cli_entry_file_exposes_help(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["raidar", "--help"])
    monkeypatch.setenv("RAIDAR_SECRETS_SOURCE", "infisical")

    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(Path(process.__file__).with_name("__init__.py")), run_name="__main__")

    assert stopped.value.code == 0
    output = capsys.readouterr().out
    assert "Usage:" in output
    assert "lock" in output
    assert "assess" in output


@pytest.mark.parametrize("returncode", [0, 7, -15])
def test_external_command_failure_stops_the_cli_before_completion(
    monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    monkeypatch.setattr(
        process.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode),
    )

    @click.command()
    def command() -> None:
        process.run_or_raise(["tool", "check"], Path("/workspace"))
        click.echo("completed")

    result = CliRunner().invoke(command)

    assert result.exit_code == (0 if returncode == 0 else 1)
    if returncode == 0:
        assert result.output.endswith("completed\n")
    else:
        assert "completed" not in result.output
        assert f"Command failed ({returncode}): tool check" in result.output
