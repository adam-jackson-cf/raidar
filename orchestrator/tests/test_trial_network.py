"""Native Harbor startup and phase policies enforce the trial's host contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from harbor.models.task.config import NetworkMode, StepConfig, TaskConfig, VerifierEnvironmentMode
from harbor.models.trial.config import AgentConfig, EnvironmentConfig
from harbor.trial.network_policy import resolve_trial_network_plan

from raidar.engine.trial.network import restrict_trial_network


@pytest.mark.parametrize(
    ("provider", "inference_hosts"),
    [
        ("openai", {"api.openai.com", "chatgpt.com", "auth.openai.com"}),
        ("anthropic", {"api.anthropic.com"}),
    ],
)
def test_native_startup_and_phase_policies_only_allow_declared_hosts(
    tmp_path: Path, provider: str, inference_hosts: set[str]
) -> None:
    task = TaskConfig(steps=[StepConfig(name="first"), StepConfig(name="second")])
    task.agent.network_mode = NetworkMode.PUBLIC
    task.verifier.network_mode = NetworkMode.PUBLIC
    for step in task.steps or ():
        step.agent.network_mode = NetworkMode.PUBLIC
        step.verifier.network_mode = NetworkMode.PUBLIC
    path = tmp_path / "task.toml"
    path.write_text(task.model_dump_toml())

    restrict_trial_network(tmp_path, runtime="python", provider=provider)

    rendered = TaskConfig.model_validate_toml(path.read_text())
    expected = inference_hosts | {"pypi.org", "files.pythonhosted.org", "registry.npmjs.org"}
    for step in [None, *(rendered.steps or ())]:
        plan = resolve_trial_network_plan(
            rendered,
            AgentConfig(),
            EnvironmentConfig(),
            step,
            verifier_mode=VerifierEnvironmentMode.SHARED,
        )
        for policy in (plan.agent_env_baseline, plan.agent_phase, plan.verifier_phase):
            assert policy.network_mode == NetworkMode.ALLOWLIST
            assert set(policy.allowed_hosts) == expected
            assert "unapproved.example.org" not in policy.allowed_hosts


@pytest.mark.parametrize("restricted_level", ["environment", "agent", "step"])
def test_explicit_public_overrides_cannot_widen_stricter_parent_policies(
    tmp_path: Path, restricted_level: str
) -> None:
    task = TaskConfig(steps=[StepConfig(name="first")])
    step = task.steps[0]
    task.agent.network_mode = NetworkMode.PUBLIC
    step.agent.network_mode = NetworkMode.PUBLIC
    if restricted_level == "environment":
        task.environment.network_mode = NetworkMode.NO_NETWORK
    elif restricted_level == "agent":
        task.agent.network_mode = NetworkMode.NO_NETWORK
    else:
        step.agent.network_mode = NetworkMode.NO_NETWORK
    path = tmp_path / "task.toml"
    path.write_text(task.model_dump_toml())

    restrict_trial_network(tmp_path, runtime="python", provider="openai")

    rendered = TaskConfig.model_validate_toml(path.read_text())
    plan = resolve_trial_network_plan(
        rendered,
        AgentConfig(),
        EnvironmentConfig(),
        rendered.steps[0],
        verifier_mode=VerifierEnvironmentMode.SHARED,
    )
    assert plan.agent_phase.network_mode == NetworkMode.NO_NETWORK
    assert plan.agent_phase.allowed_hosts == []
    if restricted_level == "environment":
        assert plan.agent_env_baseline.network_mode == NetworkMode.NO_NETWORK
        assert plan.verifier_phase.network_mode == NetworkMode.NO_NETWORK


def test_existing_allowlists_intersect_the_contract_and_preserve_narrower_phase_hosts(
    tmp_path: Path,
) -> None:
    task = TaskConfig(steps=[StepConfig(name="first")])
    task.environment.network_mode = NetworkMode.ALLOWLIST
    task.environment.allowed_hosts = ["*.openai.com", "pypi.org", "unapproved.example.org"]
    task.agent.network_mode = NetworkMode.ALLOWLIST
    task.agent.allowed_hosts = ["api.openai.com", "unapproved.example.org"]
    task.steps[0].agent.network_mode = NetworkMode.PUBLIC
    path = tmp_path / "task.toml"
    path.write_text(task.model_dump_toml())

    restrict_trial_network(tmp_path, runtime="python", provider="openai")

    rendered = TaskConfig.model_validate_toml(path.read_text())
    plan = resolve_trial_network_plan(
        rendered,
        AgentConfig(),
        EnvironmentConfig(),
        rendered.steps[0],
        verifier_mode=VerifierEnvironmentMode.SHARED,
    )
    assert set(plan.agent_env_baseline.allowed_hosts) == {
        "api.openai.com",
        "auth.openai.com",
        "pypi.org",
    }
    assert set(plan.agent_phase.allowed_hosts) == {"api.openai.com"}


def test_unknown_runtime_or_provider_contracts_are_refused_before_task_rewrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "task.toml"
    original = TaskConfig().model_dump_toml()
    path.write_text(original)
    for runtime, provider in (("unknown", "openai"), ("python", "unknown")):
        with pytest.raises(ValueError, match="no trial network host contract"):
            restrict_trial_network(tmp_path, runtime=runtime, provider=provider)
        assert path.read_text() == original
