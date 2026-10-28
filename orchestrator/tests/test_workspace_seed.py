"""Workspace seeding (D2): the project the lab writes into ``/app`` and how a trial proves it."""

from __future__ import annotations

import configparser
import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from raidar.contract.trial import CheckpointState, PlannedTrial, TrialHashes
from raidar.design.experiment.schema import ProtocolSpec
from raidar.engine.trial.collect import TrialContext, classify_trial, execution_validity
from raidar.engine.trial.progression import progression_rule
from raidar.engine.trial.workspace import (
    SEED_SLUG,
    pip_config,
    seed_check,
    seed_locks,
    seeded_digest,
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
from tests.execution_support import (
    SEED_DIGEST,
    SEED_SHA,
    StepOutcome,
    write_seed_record,
    write_trial,
)

STEPS = ("checkpoint_1", "checkpoint_2")
RULE = progression_rule(ProtocolSpec())
PINNED = "0.156.1"
CUTOFF = "2026-10-27T12:00:00Z"
_LOCKS = {"amd64": "# amd64 lock\n", "arm64": "# arm64 lock\n"}


def _context(task_dir: Path) -> TrialContext:
    hashes = TrialHashes(
        lock="l",
        problem="p",
        arm="a",
        workflow="w",
        packages="s",
        primitive_code="s",
        agent="g",
        protocol="r",
        trial="t",
    )
    trial = PlannedTrial(
        trial_id="b00-p0-baseline",
        attempt=0,
        order=0,
        sequence=0,
        arm="baseline",
        problem="scb/adder@fixture",
        problem_index=0,
        steps=STEPS,
        hashes=hashes,
    )
    return TrialContext(
        trial=trial,
        planned_steps=tuple((step, step) for step in STEPS),
        task_dir=task_dir,
        provisions=(SEED_SLUG,),
        pinned_harness_version=PINNED,
        rule=RULE,
    )


def _outcomes() -> list[StepOutcome]:
    return [StepOutcome("checkpoint_1"), StepOutcome("checkpoint_2")]


@pytest.mark.parametrize("ref", ["scb/forge@38d627ecf668", "scb/circuit_eval-checkpoint.1@x_y"])
def test_the_workspace_project_name_is_a_valid_pep_508_name_for_any_problem_ref(ref: str) -> None:
    name = tomllib.loads(workspace_project(ref, [], "3.12", CUTOFF))["project"]["name"]

    assert re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", name)
    assert name.endswith("-workspace")
    assert "@" not in name


def test_the_workspace_project_name_for_a_pinned_ref() -> None:
    document = tomllib.loads(workspace_project("scb/forge@38d627ecf668", [], "3.12", CUTOFF))

    assert document["project"]["name"] == "scb-forge-38d627ecf668-workspace"


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


@pytest.mark.parametrize("locked", [True, False])
def test_the_seed_install_script_is_valid_shell(locked: bool) -> None:
    seed = workspace_seed(
        "scb/adder", ["numpy"] if locked else [], "3.12", CUTOFF, _LOCKS if locked else {}
    )

    for script in (seed.install, seed.check):
        subprocess.run(["sh", "-n", "-c", script], check=True)


def test_the_seed_provision_carries_the_project_locks_and_what_it_seeded() -> None:
    seed = workspace_seed("scb/adder", ["numpy"], "3.12", CUTOFF, _LOCKS)

    assert seed.slug == SEED_SLUG
    assert seed.snapshot == "/app"
    assert (
        seed.payload["pyproject.toml"].content
        == workspace_project("scb/adder", ["numpy"], "3.12", CUTOFF).encode()
    )
    assert seed.payload["pylock.amd64.toml"].content == b"# amd64 lock\n"
    assert seed.payload["pylock.arm64.toml"].content == b"# arm64 lock\n"
    assert seed.facts["dependencies"] == ["numpy"]
    assert seed.facts["python"] == "3.12"
    assert seed.facts["exclude_newer"] == CUTOFF
    assert seed.facts["lock_sha256"] == {a: lock_sha256(t) for a, t in _LOCKS.items()}
    assert len(seed.facts["pyproject_sha256"]) == 64


def test_the_seed_check_passes_on_a_recorded_seed_and_fails_otherwise(tmp_path: Path) -> None:
    trial = tmp_path / "trial"
    agent_dir = trial / "steps" / "checkpoint_1" / "agent"
    assert seed_check(agent_dir)[0] is False  # no record at all
    assert seeded_digest(agent_dir) is None

    write_seed_record(trial, "checkpoint_1")
    assert seed_check(agent_dir)[0] is True
    assert seeded_digest(agent_dir) == SEED_DIGEST

    write_seed_record(trial, "checkpoint_1", found_sha="0" * 64)
    passed, detail = seed_check(agent_dir)
    assert passed is False
    assert SEED_SHA in detail

    write_seed_record(trial, "checkpoint_1", install_ok=False)
    assert seed_check(agent_dir)[0] is False  # the install never produced the file


def test_the_seed_check_fails_when_the_agents_pip_cannot_honour_the_cutoff(tmp_path: Path) -> None:
    trial = tmp_path / "trial"
    log = write_seed_record(trial, "checkpoint_1")
    record = json.loads(log.read_text())
    record["check"]["values"]["pip_cutoff"] = "unsupported"
    log.write_text(json.dumps(record) + "\n")

    passed, detail = seed_check(trial / "steps" / "checkpoint_1" / "agent")

    assert passed is False
    assert "cutoff" in detail


def test_checkpoint_one_starts_from_the_seeded_workspace(tmp_path: Path) -> None:
    trial_dir = write_trial(tmp_path / "trial", _outcomes(), seeded=True)

    first, second = classify_trial(trial_dir, _context(tmp_path))

    assert first.checkpoint_state is CheckpointState.SOLVED
    assert first.parent_snapshot_digest == SEED_DIGEST
    assert first.snapshot is not None
    assert second.parent_snapshot_digest == first.snapshot.digest
    validity = execution_validity(trial_dir, (first, second), PINNED, RULE)
    assert {check.name: check.passed for check in validity.checks}["lab_checks"] is True


@pytest.mark.parametrize("damage", ["missing", "failed-install", "file-changed"])
def test_a_trial_whose_seed_is_missing_or_failed_is_an_infra_error_and_invalid(
    tmp_path: Path, damage: str
) -> None:
    trial_dir = write_trial(tmp_path / "trial", _outcomes(), seeded=True)
    record = trial_dir / "steps" / "checkpoint_1" / "agent" / "raidar-provision"
    if damage == "missing":
        (record / f"{SEED_SLUG}.jsonl").unlink()
    elif damage == "failed-install":
        write_seed_record(trial_dir, "checkpoint_1", install_ok=False)
    else:
        write_seed_record(trial_dir, "checkpoint_1", found_sha="absent")

    first, second = classify_trial(trial_dir, _context(tmp_path))

    assert first.checkpoint_state is CheckpointState.INFRA_ERROR
    assert first.infra_reasons
    assert all(reason.startswith("lab_check:") for reason in first.infra_reasons)
    assert any(SEED_SLUG in reason for reason in first.infra_reasons)
    assert first.parent_snapshot_digest == (SEED_DIGEST if damage == "file-changed" else None)
    assert second.checkpoint_state is CheckpointState.SOLVED
    validity = execution_validity(trial_dir, (first, second), PINNED, RULE)
    checks = {check.name: check for check in validity.checks}
    assert checks["lab_checks"].passed is False
    assert not validity.passed
