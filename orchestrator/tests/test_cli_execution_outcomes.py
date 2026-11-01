"""CLI outcomes of ``agent``, ``env``, ``plan`` and ``run``, with injected runners and plans."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from raidar_contract.agent import AgentName
from raidar_contract.agent_records import Credentials

from raidar.cli import env_harbor, main
from raidar.cli.target_options import docker_selection, job_target
from raidar.engine.job.run_checks import RunRefusedError
from raidar.engine.target.guard import TargetLostError
from raidar.engine.target.health import TargetUnavailableError
from raidar.engine.trial.environment import trial_project_name

TARGETS = """
default = "remote"

[targets.remote]
docker_context = "remote-ctx"
trusted = true
grader = "local"

[targets.local]
docker_context = "local-ctx"
trusted = true
"""


# --- agent ------------------------------------------------------------------------------------


def test_agent_list_names_every_supported_agent_with_its_models() -> None:
    result = CliRunner().invoke(main, ["agent", "list"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("Supported agents:")
    for name in AgentName:
        assert f"  {name.value:12} ->" in result.output
    assert result.output.count("models:") == len(AgentName)


def test_agent_validate_reports_a_requested_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODEX_CLI_PATH", sys.executable)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))

    result = CliRunner().invoke(
        main,
        ["agent", "validate", "-a", "codex", "-p", "openai", "-m", "gpt-5.4-mini"]
        + ["--reasoning-effort", "low"],
    )

    assert result.exit_code == 0, result.output
    assert "reasoning_effort: low" in result.output


def test_agent_auth_setup_is_refused_for_an_agent_without_interactive_login() -> None:
    other = next(name.value for name in AgentName if name is not AgentName.CODEX)

    result = CliRunner().invoke(main, ["agent", "setup-auth", "--agent", other])

    assert result.exit_code == 1
    assert "Agent auth setup is only implemented for codex." in result.output


def test_agent_auth_setup_is_refused_when_the_harness_keeps_no_session_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "raidar.cli.agent.credentials", lambda harness, env: Credentials(None, session_file=None)
    )

    result = CliRunner().invoke(main, ["agent", "setup-auth", "--agent", "codex"])

    assert result.exit_code == 1
    assert "no file-backed session login location" in result.output


def test_a_failed_codex_login_reports_its_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.setenv("CODEX_CLI_PATH", sys.executable)
    ran: list[list[str]] = []

    def failing_login(command: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        ran.append(command)
        return subprocess.CompletedProcess(command, 3)

    monkeypatch.setattr("raidar.cli.agent.subprocess.run", failing_login)

    result = CliRunner().invoke(main, ["agent", "setup-auth", "--agent", "codex"])

    assert result.exit_code == 1
    assert "Codex login failed with exit code 3" in result.output
    assert [command[1:] for command in ran] == [["login"]]
    assert "setup complete" not in result.output


# --- env setup ---------------------------------------------------------------------------------


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every subprocess the setup command starts, answered by a fake runner that succeeds."""
    ran: list[list[str]] = []
    monkeypatch.setattr(env_harbor, "compose_preflight_reason", lambda: None)
    monkeypatch.setattr(env_harbor, "harbor_executable", lambda: "/venv/bin/harbor")

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        ran.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="0.9.1\n")

    monkeypatch.setattr("raidar.cli.env_harbor.subprocess.run", run)
    return ran


def test_env_setup_installs_the_toolchain_then_reports_the_harbor_version(
    commands: list[list[str]],
) -> None:
    result = CliRunner().invoke(main, ["env", "setup", "--sync-arg=--no-dev"])

    assert result.exit_code == 0, result.output
    assert commands == [
        ["uv", "python", "install", "3.12"],
        ["uv", "sync", "--frozen", "--no-dev"],
        ["/venv/bin/harbor", "--version"],
    ]
    assert "harbor 0.9.1" in result.output
    assert result.output.rstrip().endswith("Environment setup completed.")


def test_env_setup_without_tool_install_only_checks_harbor(commands: list[list[str]]) -> None:
    result = CliRunner().invoke(main, ["env", "setup", "--no-install-tools"])

    assert result.exit_code == 0, result.output
    assert commands == [["/venv/bin/harbor", "--version"]]


def test_env_setup_stops_at_the_first_failed_install_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(env_harbor, "compose_preflight_reason", lambda: None)
    monkeypatch.setattr(
        "raidar.cli.env_harbor.subprocess.run",
        lambda command, **_: ran.append(command) or subprocess.CompletedProcess(command, 2),
    )

    result = CliRunner().invoke(main, ["env", "setup"])

    assert result.exit_code == 1
    assert "Command failed (2): uv python install 3.12" in result.output
    assert ran == [["uv", "python", "install", "3.12"]]


def test_env_setup_explains_a_missing_harbor_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env_harbor, "compose_preflight_reason", lambda: None)

    def missing() -> str:
        raise FileNotFoundError("project-pinned Harbor CLI not found at /venv/bin/harbor")

    monkeypatch.setattr(env_harbor, "harbor_executable", missing)

    result = CliRunner().invoke(main, ["env", "setup", "--no-install-tools"])

    assert result.exit_code == 1
    assert "not found at /venv/bin/harbor; run `make env-setup` to sync it." in result.output


def test_env_setup_refuses_a_harbor_cli_that_will_not_report_its_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env_harbor, "compose_preflight_reason", lambda: None)
    monkeypatch.setattr(env_harbor, "harbor_executable", lambda: "/venv/bin/harbor")
    monkeypatch.setattr(
        "raidar.cli.env_harbor.subprocess.run",
        lambda command, **_: subprocess.CompletedProcess(command, 1, stdout=""),
    )

    result = CliRunner().invoke(main, ["env", "setup", "--no-install-tools"])

    assert result.exit_code == 1
    assert "Project-pinned Harbor CLI is unavailable" in result.output
    assert "Environment setup completed." not in result.output


def test_a_compose_that_fails_or_hangs_does_not_block_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "raidar.cli.env_harbor.subprocess.run",
        lambda command, **_: subprocess.CompletedProcess(command, 1, stdout="1.0.0\n"),
    )
    assert env_harbor.compose_preflight_reason() is None

    def hang(command: list[str], **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(command, 15)

    monkeypatch.setattr("raidar.cli.env_harbor.subprocess.run", hang)
    assert env_harbor.compose_preflight_reason() is None


# --- target selection --------------------------------------------------------------------------


@pytest.fixture
def split_targets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple[str, str]]:
    """A trial target graded elsewhere; ensure/sweep calls are recorded in place of Docker."""
    (tmp_path / "targets.toml").write_text(TARGETS)
    monkeypatch.setenv("RAIDAR_TARGETS_FILE", str(tmp_path / "targets.toml"))
    monkeypatch.delenv("RAIDAR_TARGET", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "before")  # restored with the test
    steps: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "raidar.cli.target_options.ensure_target", lambda t: steps.append(("ensure", t.name))
    )
    monkeypatch.setattr(
        "raidar.cli.target_options.sweep_grader_orphans", lambda ctx: steps.append(("sweep", ctx))
    )
    return steps


def test_a_separate_grader_target_is_ensured_and_swept_for_orphans(
    split_targets: list[tuple[str, str]],
) -> None:
    selection = docker_selection("remote", grader_purpose="hidden-test grading")

    assert (selection.trial.name, selection.grader.name) == ("remote", "local")
    assert split_targets == [("ensure", "remote"), ("ensure", "local"), ("sweep", "local-ctx")]

    assert os.environ["DOCKER_CONTEXT"] == "remote-ctx"


@pytest.fixture
def job_target_world(
    monkeypatch: pytest.MonkeyPatch, split_targets: list[tuple[str, str]]
) -> SimpleNamespace:
    """``job_target`` over the split targets; the Docker daemon is a fake that can go down."""
    world = SimpleNamespace(down=False, reaped=[], ensured=[])
    monkeypatch.setattr("raidar.cli.target_options.docker_facts", lambda target: None)
    monkeypatch.setattr(
        "raidar.cli.target_options.reap_project",
        lambda project, docker_context: world.reaped.append((project, docker_context)) or {},
    )

    def ensure(target: Any, *, timeout_s: float) -> None:
        world.ensured.append(target.name)
        if world.down:
            raise TargetUnavailableError("daemon down")

    monkeypatch.setattr("raidar.engine.target.guard.ensure_target", ensure)
    monkeypatch.setenv("RAIDAR_TARGET_RECONNECT_S", "0")
    return world


def test_a_run_trusts_a_remote_trial_target_graded_on_a_local_one(
    job_target_world: SimpleNamespace,
) -> None:
    target = job_target(None)

    assert (target.selection.trial.name, target.selection.grader.name) == ("remote", "local")
    assert target.manifest_fields(8)["execution_target"]["grader"]["docker_context"] == "local-ctx"


def test_the_job_guard_admits_trials_while_the_target_is_up_and_stops_the_job_when_it_is_lost(
    job_target_world: SimpleNamespace,
) -> None:
    guard = job_target(None).guard
    assert guard is not None

    asyncio.run(guard.admit("b00-p0-a"))
    assert job_target_world.ensured == ["remote"]

    job_target_world.down = True
    with pytest.raises(TargetLostError, match="'remote' stayed down for 0s: daemon down"):
        asyncio.run(guard.admit("b00-p0-b"))
    with pytest.raises(TargetLostError):
        asyncio.run(guard.admit("b01-p0-a"))
    assert guard.outages == 1


def test_the_job_guard_cleans_up_the_compose_project_harbor_named_for_the_trial(
    job_target_world: SimpleNamespace, tmp_path: Path
) -> None:
    guard = job_target(None).guard
    assert guard is not None

    guard.reap("b00-p0-a", tmp_path)
    guard.reap("b00-p0-b", tmp_path)

    projects = [trial_project_name(t, tmp_path) for t in ("b00-p0-a", "b00-p0-b")]
    assert len(set(projects)) == 2, "each trial has its own compose project"
    assert job_target_world.reaped == [(p, "remote-ctx") for p in projects]


# --- plan and run ------------------------------------------------------------------------------


def _fake_plan(*, selected: frozenset[str], options: Any = None) -> Any:
    def hashes(tag: str) -> Any:
        return SimpleNamespace(
            **{
                name: f"{tag}-{name}-0123456789abcdef"
                for name in ("trial", "arm", "workflow", "packages", "primitive_code")
            },
            problem=f"{tag}-problem-0123456789abcdef",
            agent=f"{tag}-agent-0123456789abcdef",
            protocol=f"{tag}-protocol-0123456789abcdef",
        )

    def trial(sequence: int, arm: str) -> Any:
        return SimpleNamespace(
            trial_id=f"b00-p0-{arm}",
            sequence=sequence,
            attempt=0,
            order=sequence,
            steps=("s1", "s2"),
            hashes=hashes(arm),
        )

    arm = SimpleNamespace(
        identity=lambda: {"agent_release": "acme/agent@1", "workflow": "wf@2", "digest": "f" * 40}
    )
    blueprint = SimpleNamespace(arm=arm, agent_config=SimpleNamespace(name=None, import_path="x:Y"))
    return SimpleNamespace(
        experiment_id="exp-1",
        lock_sha256="a" * 64,
        rule=SimpleNamespace(mode="continue"),
        concurrency=3,
        options=options or SimpleNamespace(max_checkpoints=None, agent_override=None),
        arms={"a": blueprint},
        trials=(trial(0, "a"), trial(1, "b")),
        selected=selected,
        to_json=lambda: {"kind": "raidar-plan", "experiment": "exp-1"},
    )


@pytest.fixture
def experiment_file(tmp_path: Path) -> Path:
    path = tmp_path / "experiment.yaml"
    path.write_text("id: exp-1\n")
    return path


def test_plan_lists_every_trial_marking_the_selected_ones(
    monkeypatch: pytest.MonkeyPatch, experiment_file: Path
) -> None:
    plan = _fake_plan(selected=frozenset({"b00-p0-b"}))
    monkeypatch.setattr("raidar.cli.execution.build_cli_plan", lambda options: (plan, None))

    result = CliRunner().invoke(main, ["plan", str(experiment_file)])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == f"Plan for exp-1 (lock {'a' * 12})"
    assert "progression: continue  concurrency: 3" in lines[1]
    assert any(line.startswith("  arm a: agent acme/agent@1 workflow wf@2") for line in lines)
    assert any("harbor x:Y" in line for line in lines)
    assert any(line.startswith("   000 b00-p0-a attempt=0") for line in lines)
    assert any(line.startswith(" * 001 b00-p0-b attempt=0") for line in lines)
    assert lines[-1] == "  1/2 trials selected (*)"
    assert "truncated_to" not in result.output


def test_plan_states_the_truncation_and_agent_override_it_runs_under(
    monkeypatch: pytest.MonkeyPatch, experiment_file: Path
) -> None:
    options = SimpleNamespace(max_checkpoints=2, agent_override="nop")
    plan = _fake_plan(selected=frozenset(), options=options)
    monkeypatch.setattr("raidar.cli.execution.build_cli_plan", lambda options: (plan, None))

    result = CliRunner().invoke(main, ["plan", str(experiment_file)])

    assert result.exit_code == 0, result.output
    assert "  truncated_to: 2  agent_override: nop" in result.output
    assert "0/2 trials selected (*)" in result.output


def test_plan_json_emits_the_plan_document_instead_of_the_listing(
    monkeypatch: pytest.MonkeyPatch, experiment_file: Path
) -> None:
    plan = _fake_plan(selected=frozenset())
    monkeypatch.setattr("raidar.cli.execution.build_cli_plan", lambda options: (plan, None))

    result = CliRunner().invoke(main, ["plan", str(experiment_file), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"kind": "raidar-plan", "experiment": "exp-1"}


def test_plan_refuses_a_concurrency_below_one(experiment_file: Path) -> None:
    result = CliRunner().invoke(main, ["plan", str(experiment_file), "--concurrency", "0"])

    assert result.exit_code == 2
    assert "--concurrency" in result.output


def test_run_refuses_a_selection_that_matches_no_trial_before_touching_a_target(
    monkeypatch: pytest.MonkeyPatch, experiment_file: Path
) -> None:
    plan = _fake_plan(selected=frozenset())
    monkeypatch.setattr("raidar.cli.execution.build_cli_plan", lambda options: (plan, None))

    def no_docker(_name: object) -> None:
        raise AssertionError("the target was resolved for an empty selection")

    monkeypatch.setattr("raidar.cli.execution.job_target", no_docker)

    result = CliRunner().invoke(main, ["run", str(experiment_file)])

    assert result.exit_code == 1
    assert "the selection matches no trial" in result.output


def test_run_reports_an_admission_refusal_without_starting_the_job(
    monkeypatch: pytest.MonkeyPatch, experiment_file: Path, tmp_path: Path
) -> None:
    plan = _fake_plan(selected=frozenset({"b00-p0-a"}))
    monkeypatch.setattr("raidar.cli.execution.build_cli_plan", lambda options: (plan, "store"))
    monkeypatch.setattr("raidar.cli.execution.job_target", lambda name: "target")

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RunRefusedError("no clean smoke job matches this experiment")

    def never_started(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a refused run started its job")

    monkeypatch.setattr("raidar.cli.execution.prepare_run", refuse)
    monkeypatch.setattr("raidar.cli.execution.run_job", never_started)

    result = CliRunner().invoke(
        main, ["run", str(experiment_file), "--jobs-dir", str(tmp_path / "jobs")]
    )

    assert result.exit_code == 1
    assert "no clean smoke job matches this experiment" in result.output


def test_run_prints_each_trials_validity_reach_and_checkpoint_states(
    monkeypatch: pytest.MonkeyPatch, experiment_file: Path, tmp_path: Path
) -> None:
    plan = _fake_plan(selected=frozenset({"b00-p0-a"}))
    seen: dict[str, Any] = {}
    record = SimpleNamespace(
        trial=SimpleNamespace(trial_id="b00-p0-a"),
        valid=True,
        reached=[True, False],
        step_results=[
            SimpleNamespace(checkpoint_state=SimpleNamespace(value=state))
            for state in ("passed", "failed")
        ],
    )

    def prepare(plan_arg: Any, store: Any, target: Any, jobs_dir: Path, *, allow_unsmoked: bool):
        seen.update(store=store, target=target, allow_unsmoked=allow_unsmoked)
        return {0: "problem"}, {"admitted": True}

    async def run_job(plan_arg: Any, problems: Any, **kwargs: Any) -> list[Any]:
        seen.update(job_id=kwargs["job_id"], job_dir=kwargs["job_dir"], problems=problems)
        return [record]

    monkeypatch.setattr("raidar.cli.execution.build_cli_plan", lambda options: (plan, "store"))
    monkeypatch.setattr("raidar.cli.execution.job_target", lambda name: f"target:{name}")
    monkeypatch.setattr("raidar.cli.execution.prepare_run", prepare)
    monkeypatch.setattr("raidar.cli.execution.run_job", run_job)
    jobs = tmp_path / "jobs"

    result = CliRunner().invoke(
        main,
        ["run", str(experiment_file), "--jobs-dir", str(jobs), "--job-id", "j1"]
        + ["--target", "lab", "--allow-unsmoked"],
    )

    assert result.exit_code == 0, result.output
    job_dir = jobs.resolve() / "exp-1" / "j1"
    assert seen["job_dir"] == job_dir
    assert seen["allow_unsmoked"] is True
    assert seen["target"] == "target:lab"
    assert result.output.splitlines() == [
        f"Job {job_dir}",
        "  b00-p0-a: valid=True reached=1 [passed failed]",
    ]


def test_run_names_the_job_after_the_clock_when_no_id_is_given(
    monkeypatch: pytest.MonkeyPatch, experiment_file: Path, tmp_path: Path
) -> None:
    plan = _fake_plan(selected=frozenset({"b00-p0-a"}))
    monkeypatch.setattr("raidar.cli.execution.build_cli_plan", lambda options: (plan, "store"))
    monkeypatch.setattr("raidar.cli.execution.job_target", lambda name: "target")
    monkeypatch.setattr("raidar.cli.execution.default_job_id", lambda: "20260101T000000Z")
    monkeypatch.setattr("raidar.cli.execution.prepare_run", lambda *a, **k: ({}, {}))

    async def run_job(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr("raidar.cli.execution.run_job", run_job)

    result = CliRunner().invoke(
        main, ["run", str(experiment_file), "--jobs-dir", str(tmp_path / "jobs")]
    )

    assert result.exit_code == 0, result.output
    job_dir = tmp_path.resolve() / "jobs" / "exp-1" / "20260101T000000Z"
    assert result.output.strip() == f"Job {job_dir}"
