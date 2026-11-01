"""Running a job's trials: start order, concurrency, guard wiring, job directory records."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from harbor.models.trial.config import AgentConfig, TrialConfig

from raidar.engine.job.provenance import execution_source_digest
from raidar.engine.trial import driver
from raidar.engine.trial.agent_config import reported_harness_pin
from raidar.engine.trial.driver import TrialRun, run_harbor_trial
from raidar.engine.trial.environment import RunScopedDockerEnvironment, run_scope
from raidar.engine.trial.host_auth import CODEX_AUTH_ENV, with_host_auth
from raidar.engine.trial.provision import TrialProvision
from raidar.subject.agent.config import Harness
from raidar.subject.workflow.runner import WorkflowRunner


def test_the_leak_guard_is_installed_before_the_trial_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class FakeTrial:
        class paths:  # noqa: N801 (mirrors Harbor's attribute)
            trial_dir = tmp_path / "t"

        async def run(self) -> Any:
            events.append("run")
            return type("Result", (), {"exception_info": None})()

    async def create(config: TrialConfig) -> FakeTrial:
        events.append("create")
        return FakeTrial()

    def guard(trial: Any, bundle: Any) -> None:
        events.append(f"guard:{bundle}")

    monkeypatch.setattr(driver.Trial, "create", create)
    monkeypatch.setattr(driver, "install_leak_guard", guard)
    monkeypatch.setattr(
        driver, "install_agent_reaper", lambda trial, bundle: events.append("reaper")
    )
    monkeypatch.setattr(
        driver,
        "install_trial_provisioning",
        lambda trial, provisions: events.append(f"provision:{len(provisions)}"),
    )
    monkeypatch.setattr(
        driver, "install_tool_tree_watch", lambda trial, bundle: events.append("tool-tree")
    )
    monkeypatch.setattr(driver, "install_liveness_monitor", lambda trial: events.append("liveness"))
    provisions = (TrialProvision("lint-tool", {}, "true", "true"),)
    outcome = asyncio.run(
        run_harbor_trial(TrialConfig.model_construct(), tmp_path / "bundle", provisions)
    )
    assert events == [
        "create",
        f"guard:{tmp_path / 'bundle'}",
        "reaper",
        "provision:1",
        "tool-tree",
        "liveness",
        "run",
    ]
    assert outcome == TrialRun(tmp_path / "t", None)


def test_codex_gets_the_host_session_file_path_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text('{"tokens": {"access_token": "x"}}')
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_AUTH_MODE", "auto")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    codex = AgentConfig(name="codex")
    assert with_host_auth(codex, Harness.CODEX_CLI).env == {CODEX_AUTH_ENV: str(auth)}
    assert with_host_auth(AgentConfig(name="oracle"), Harness.CODEX_CLI).env == {}
    wrapper = AgentConfig(
        import_path=WorkflowRunner.import_path(),
        kwargs={"inner_agent": {"name": "codex", "kwargs": {"version": "0.156.1"}}},
    )
    # A workflow wrapper is held to its wrapped harness's pin; a no-LLM override has none.
    assert reported_harness_pin(wrapper) == "0.156.1"
    assert reported_harness_pin(AgentConfig(name="codex", kwargs={"version": "0.156.1"})) == (
        "0.156.1"
    )
    assert reported_harness_pin(AgentConfig(name="oracle")) is None
    # The runner passes its env to every inner turn: without
    # the session file its inner codex called the API with an empty key (401, pilot).
    assert with_host_auth(wrapper, Harness.CODEX_CLI).env == {CODEX_AUTH_ENV: str(auth)}
    monkeypatch.setenv("CODEX_AUTH_MODE", "api")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert with_host_auth(codex, Harness.CODEX_CLI).env == {}


def test_run_scoped_environment_suffixes_the_compose_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same trial id in two job dirs -> two Compose projects (the pilot's exit-143 collision)."""
    first, second = tmp_path / "exp-a" / "r1" / "trials", tmp_path / "exp-b" / "r1" / "trials"
    assert run_scope(first) == run_scope(first)
    assert run_scope(first) != run_scope(second)
    seen: list[str] = []

    def fake_init(self: object, *args: Any, session_id: str, **kwargs: Any) -> None:
        seen.append(session_id)

    monkeypatch.setattr("harbor.environments.docker.docker.DockerEnvironment.__init__", fake_init)
    for trials in (first, second):
        RunScopedDockerEnvironment(session_id="b00-p0-baseline__env", run_scope=run_scope(trials))
    assert seen == [f"b00-p0-baseline__env-{run_scope(t)}" for t in (first, second)]
    assert len(set(seen)) == 2


@pytest.mark.parametrize("owner", ["engine/health", "grading", "subject/agent", "contract"])
def test_execution_provenance_tracks_behavior_and_source_paths_but_not_documentation(
    tmp_path: Path, owner: str
) -> None:
    root = tmp_path / "raidar"
    source = root / owner / "operation.py"
    source.parent.mkdir(parents=True)
    source.write_text('"""Operation."""\nLIMIT = 1\n')
    initial = execution_source_digest(root)
    source.write_text('"""Reworded operation."""\n# Clarification.\nLIMIT = 1\n')
    assert execution_source_digest(root) == initial
    source.write_text("LIMIT = 2\n")
    changed = execution_source_digest(root)
    assert changed != initial
    source.rename(source.with_name("renamed.py"))
    assert execution_source_digest(root) != changed


def test_execution_provenance_refuses_an_empty_source_tree(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no Raidar Python source"):
        execution_source_digest(tmp_path)
