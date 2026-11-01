"""Workspace seeding (D2): the project the lab writes into ``/app`` and how a trial proves it."""

from __future__ import annotations

import configparser
import re
import tomllib
from pathlib import Path

import pytest

from raidar.engine.trial.workspace import (
    pip_config,
    seed_locks,
    uv_config,
    workspace_project,
    workspace_seed,
)
from raidar.grading.dependency_pins import (
    DependencyLockError,
    DependencyLocks,
    DependencyPins,
    DependencySet,
    lock_sha256,
    set_sha256,
)

CUTOFF = "2026-10-27T12:00:00Z"
_LOCKS = {"amd64": "# amd64 lock\n", "arm64": "# arm64 lock\n"}


@pytest.mark.parametrize("ref", ["scb/forge@38d627ecf668", "scb/circuit_eval-checkpoint.1@x_y"])
def test_the_workspace_project_name_is_a_valid_pep_508_name_for_any_problem_ref(ref: str) -> None:
    name = tomllib.loads(workspace_project(ref, [], "3.12", CUTOFF))["project"]["name"]

    assert re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", name)
    assert name.endswith("-workspace")
    assert "@" not in name


def test_the_workspace_project_name_tells_problem_versions_apart() -> None:
    names = {
        tomllib.loads(workspace_project(ref, [], "3.12", CUTOFF))["project"]["name"]
        for ref in ("scb/forge@38d627ecf668", "scb/forge@38d627ecf669", "scb/other@38d627ecf668")
    }

    assert len(names) == 3


def test_the_workspace_project_names_the_problem_python_and_declared_dependencies() -> None:
    document = tomllib.loads(
        workspace_project("scb/circuit_eval.v2", ["numpy>=2", "rich"], "3.12", CUTOFF)
    )

    assert document["project"] == {
        "name": "scb-circuit-eval-v2-workspace",
        "version": "0",
        "requires-python": ">=3.12",
        "dependencies": ["numpy>=2", "rich"],
    }
    assert (
        tomllib.loads(workspace_project("scb/adder", [], "3.13", CUTOFF))["project"]["dependencies"]
        == []
    )


def test_the_workspace_project_carries_the_pins_cutoff_and_refuses_source_builds() -> None:
    document = tomllib.loads(workspace_project("scb/adder", ["numpy"], "3.12", CUTOFF))

    assert document["tool"]["uv"] == {"exclude-newer": CUTOFF, "no-build": True}


def test_the_agent_users_uv_and_pip_configuration_carry_the_same_policy() -> None:
    pip = configparser.ConfigParser()
    pip.read_string(pip_config(CUTOFF))

    assert tomllib.loads(uv_config(CUTOFF)) == {
        "exclude-newer": CUTOFF,
        "no-build": True,
        "index-url": "https://pypi.org/simple",
    }
    assert dict(pip["global"]) == {
        "only-binary": ":all:",
        "index-url": "https://pypi.org/simple",
        "uploaded-prior-to": CUTOFF,
    }


def _pins(seed_requirements: list[str]) -> tuple[DependencyPins, DependencyLocks, dict[str, str]]:
    key = set_sha256(seed_requirements)
    other = set_sha256(["pytest"])
    texts = {"amd64": "# amd64 lock\n", "arm64": "# arm64 lock\n"}
    return (
        DependencyPins(
            policy="declared-wheels/1",
            resolver="uv@0.10.0",
            index="https://pypi.org/simple",
            python="3.12",
            platforms=("x86_64-manylinux_2_28", "aarch64-manylinux_2_28"),
            exclude_newer=CUTOFF,
            sets={
                key: DependencySet(
                    requirements=tuple(seed_requirements),
                    amd64=lock_sha256(texts["amd64"]),
                    arm64=lock_sha256(texts["arm64"]),
                ),
                other: DependencySet(requirements=("pytest",), amd64="0" * 64, arm64="1" * 64),
            },
            steps={"checkpoint_1": other},
            seed=key,
            grader=other,
        ),
        DependencyLocks(Path()),
        texts,
    )


def test_the_seed_reads_only_the_test_dependency_locks_of_the_pin(tmp_path: Path) -> None:
    pins, _, texts = _pins(["numpy"])
    locks = DependencyLocks(tmp_path)
    for arch, text in texts.items():
        locks.write(pins.seed or "", arch, text)  # the reference step's locks are never written

    assert seed_locks(pins, locks) == texts


def test_the_seed_refuses_a_lock_that_differs_from_its_pin(tmp_path: Path) -> None:
    pins, _, texts = _pins(["numpy"])
    locks = DependencyLocks(tmp_path)
    locks.write(pins.seed or "", "amd64", texts["amd64"])
    locks.write(pins.seed or "", "arm64", "# tampered\n")

    with pytest.raises(DependencyLockError):
        seed_locks(pins, locks)


def test_a_pin_without_test_dependencies_seeds_no_locks(tmp_path: Path) -> None:
    pins, _, _ = _pins(["numpy"])

    assert seed_locks(pins.model_copy(update={"seed": None}), DependencyLocks(tmp_path)) == {}


@pytest.mark.parametrize(
    ("dependencies", "locks"), [(["numpy"], {}), ([], {"amd64": "x", "arm64": "y"})]
)
def test_the_seed_needs_locks_exactly_when_the_problem_has_dependencies(
    dependencies: list[str], locks: dict[str, str]
) -> None:
    with pytest.raises(ValueError, match="locks"):
        workspace_seed("scb/adder", dependencies, "3.12", CUTOFF, locks)
