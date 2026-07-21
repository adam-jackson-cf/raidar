"""Behavioral tests for resolved runtime profiles and environment registries."""

from __future__ import annotations

import shutil
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from raidar.agents.config import Harness
from raidar.application.scenario_catalog import load_scenario
from raidar.runtime.environments import EnvironmentResolutionError, resolve_environment_registry
from raidar.runtime.execution_contract import resolve_effective_run_contract
from raidar.runtime.profile import (
    CleanupPolicy,
    DockerLabels,
    EnvironmentVariables,
    EvidenceLimits,
    RuntimeProfile,
)
from raidar.runtime.workspace import _workspace_runtime_env, prepare_workspace
from raidar.scorers.verification import VisualComparisonToolPolicy


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_runtime_profile_requires_deeply_frozen_subpolicies() -> None:
    profile = RuntimeProfile()

    assert isinstance(profile.compatibility_env, EnvironmentVariables)
    assert isinstance(profile.workspace_env, EnvironmentVariables)
    assert isinstance(profile.docker_labels, DockerLabels)
    assert isinstance(profile.cleanup, CleanupPolicy)
    assert isinstance(profile.evidence_limits, EvidenceLimits)
    assert profile.secret_transport_dir == _repo_root() / ".tmp/agentic-eval/secrets"
    with pytest.raises(FrozenInstanceError):
        profile.evidence_limits.file_bytes = 1  # type: ignore[misc]
    with pytest.raises(TypeError, match="compatibility_env"):
        RuntimeProfile(compatibility_env={"COMPOSE_BAKE": "true"})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="registry roots"):
        RuntimeProfile(environment_registry_roots=["environments"])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: EnvironmentVariables([]), "string pairs in a tuple"),
        (lambda: EnvironmentVariables((("A",),)), "string pairs in a tuple"),
        (lambda: EnvironmentVariables((("A", 1),)), "string pairs in a tuple"),
        (lambda: EnvironmentVariables((("", "1"),)), "must not be empty"),
        (lambda: EnvironmentVariables((("A", "1"), ("A", "2"))), "must be unique"),
        (lambda: DockerLabels(managed=1), "must be strings"),
        (lambda: DockerLabels(managed=""), "must not be empty"),
        (lambda: DockerLabels(key="io.raidar.cache.managed"), "must be unique"),
        (lambda: CleanupPolicy(container_name_prefixes=[]), "must be tuples"),
        (lambda: CleanupPolicy(build_command_marker_groups=(["build"],)), "must be tuples"),
        (lambda: CleanupPolicy(container_name_prefixes=("",)), "must not be empty"),
        (lambda: CleanupPolicy(build_command_marker_groups=((),)), "non-empty markers"),
        (lambda: CleanupPolicy(run_command_markers=("",)), "must not be empty"),
        (lambda: EvidenceLimits(file_bytes=True), "must be integers"),
        (lambda: EvidenceLimits(file_bytes=0), "must be positive"),
        (lambda: RuntimeProfile(environment_registry_roots=()), "registry roots"),
        (
            lambda: RuntimeProfile(environment_registry_roots=("environments", "environments")),
            "registry roots must be unique",
        ),
        (lambda: RuntimeProfile(docker_registry_allowlist=("GHCR.IO",)), "lowercase"),
        (
            lambda: RuntimeProfile(docker_registry_allowlist=("ghcr.io", "ghcr.io")),
            "allowlist hosts must be unique",
        ),
        (lambda: RuntimeProfile(compose_minimum_version=2), "must be a string"),
        (lambda: RuntimeProfile(compose_minimum_version="two.40.1"), "major.minor.patch"),
        (lambda: RuntimeProfile(compose_minimum_version="2.40"), "major.minor.patch"),
        (lambda: RuntimeProfile(cache_prune_interval_sec=True), "must be integers"),
        (lambda: RuntimeProfile(cache_prune_interval_sec=0), "must be positive"),
        (lambda: RuntimeProfile(secret_transport_dir="secrets"), "must be a Path"),
        (
            lambda: RuntimeProfile(secret_transport_dir=Path("secrets")),
            "must be absolute",
        ),
        (lambda: RuntimeProfile(copy_excludes=("same", "same")), "non-empty unique values"),
    ],
)
def test_runtime_profile_rejects_invalid_nested_policy(factory, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        factory()


def test_runtime_profile_mapping_and_payload_contract() -> None:
    variables = EnvironmentVariables((("A", "1"),))
    profile = RuntimeProfile()

    assert variables["A"] == "1"
    assert list(variables) == ["A"]
    assert len(variables) == 1
    assert variables.payload() == {"A": "1"}
    with pytest.raises(KeyError):
        variables["missing"]
    assert profile.cache_payload()["docker"]["labels"] == profile.docker_labels.payload()


def test_workspace_phase_behavior_changes_with_resolved_profile(tmp_path: Path) -> None:
    starter = tmp_path / "starter"
    (starter / "cache-a").mkdir(parents=True)
    (starter / "cache-b").mkdir()
    (starter / "keep.txt").write_text("keep", encoding="utf-8")
    (starter / "cache-a" / "entry").write_text("a", encoding="utf-8")
    (starter / "cache-b" / "entry").write_text("b", encoding="utf-8")
    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()
    profile_a = RuntimeProfile(
        copy_excludes=("cache-a",),
        workspace_env=EnvironmentVariables((("PROFILE_CACHE", "{runtime}/a"),)),
    )
    profile_b = RuntimeProfile(
        copy_excludes=("cache-b",),
        workspace_env=EnvironmentVariables((("PROFILE_CACHE", "{runtime}/b"),)),
    )

    workspace_a, _ = prepare_workspace(
        starter_dir=starter,
        target_dir=tmp_path / "workspace-a",
        scenario_dir=scenario_dir,
        harness=Harness.CODEX_CLI,
        runtime_profile=profile_a,
    )
    workspace_b, _ = prepare_workspace(
        starter_dir=starter,
        target_dir=tmp_path / "workspace-b",
        scenario_dir=scenario_dir,
        harness=Harness.CODEX_CLI,
        runtime_profile=profile_b,
    )
    env_a = _workspace_runtime_env(workspace_a, profile_a, {})
    env_b = _workspace_runtime_env(workspace_b, profile_b, {})

    assert not (workspace_a / "cache-a").exists()
    assert (workspace_a / "cache-b").is_dir()
    assert (workspace_b / "cache-a").is_dir()
    assert not (workspace_b / "cache-b").exists()
    assert env_a["PROFILE_CACHE"].endswith("workspace-a.runtime/a")
    assert env_b["PROFILE_CACHE"].endswith("workspace-b.runtime/b")


def test_registry_allowlist_controls_environment_admission(tmp_path: Path) -> None:
    environment_dir = tmp_path / "environments" / "private"
    environment_dir.mkdir(parents=True)
    (environment_dir / "Dockerfile").write_text(
        "FROM registry.internal.example/platform/base:1\n",
        encoding="utf-8",
    )
    (environment_dir / "environment.yaml").write_text(
        """id: private:1
version: 1
image: raidar/private:1
build:
  dockerfile: Dockerfile
capabilities: {}
""",
        encoding="utf-8",
    )

    with pytest.raises(EnvironmentResolutionError, match="unsupported registry host"):
        resolve_environment_registry(repo_root=tmp_path, runtime_profile=RuntimeProfile())

    profile = RuntimeProfile(
        environment_registry_roots=(str(tmp_path / "environments"),),
        docker_registry_allowlist=(
            *RuntimeProfile().docker_registry_allowlist,
            "registry.internal.example",
        ),
    )
    registry = resolve_environment_registry(
        repo_root=tmp_path / "separate-repository",
        runtime_profile=profile,
    )

    assert registry.roots == (tmp_path / "environments",)
    assert tuple(registry.index()) == ("private:1",)


def test_registry_entry_fingerprints_change_effective_contract(tmp_path: Path) -> None:
    source_environment = _repo_root() / "environments" / "python" / "3.12"
    target_environment = tmp_path / "environments" / "python" / "3.12"
    target_environment.parent.mkdir(parents=True)
    shutil.copytree(source_environment, target_environment)
    scenario_path = _repo_root() / "scenarios/python-code-task-baseline/v001/scenario.yaml"
    scenario = load_scenario(scenario_path)
    profile = RuntimeProfile()

    first = resolve_effective_run_contract(
        scenario=scenario,
        scenario_path=scenario_path,
        repo_root=tmp_path,
        runtime_profile=profile,
    )
    with (target_environment / "Dockerfile").open("a", encoding="utf-8") as dockerfile:
        dockerfile.write("\n# profile fingerprint change\n")
    second = resolve_effective_run_contract(
        scenario=scenario,
        scenario_path=scenario_path,
        repo_root=tmp_path,
        runtime_profile=profile,
    )

    assert first.environment_registry.roots == (tmp_path / "environments",)
    assert first.environment_registry.cache_payload() != second.environment_registry.cache_payload()
    assert first.contract_hash != second.contract_hash


def test_visual_comparison_policy_changes_effective_contract_identity(tmp_path: Path) -> None:
    source_environment = _repo_root() / "environments" / "python" / "3.12"
    target_environment = tmp_path / "environments" / "python" / "3.12"
    target_environment.parent.mkdir(parents=True)
    shutil.copytree(source_environment, target_environment)
    scenario_path = _repo_root() / "scenarios/python-code-task-baseline/v001/scenario.yaml"
    scenario = load_scenario(scenario_path)
    policy = VisualComparisonToolPolicy(
        executable="compare-images",
        arguments_template=("{reference}", "{actual}", "{diff}", "{threshold}"),
        threshold=0.2,
        timeout_sec=19,
    )

    default_contract = resolve_effective_run_contract(
        scenario=scenario,
        scenario_path=scenario_path,
        repo_root=tmp_path,
    )
    custom_contract = resolve_effective_run_contract(
        scenario=scenario,
        scenario_path=scenario_path,
        repo_root=tmp_path,
        visual_comparison_policy=policy,
    )

    assert custom_contract.visual_comparison is policy
    assert custom_contract.cache_payload["visual_comparison"] == policy.cache_payload()
    assert custom_contract.contract_hash != default_contract.contract_hash
