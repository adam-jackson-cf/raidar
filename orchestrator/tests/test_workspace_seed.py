"""Workspace seeding (D2): the project the lab writes into ``/app`` and how a trial proves it."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from raidar.contract.trial import CheckpointState, PlannedTrial, TrialHashes
from raidar.design.experiment.schema import ProtocolSpec
from raidar.engine.trial.collect import TrialContext, classify_trial, execution_validity
from raidar.engine.trial.progression import progression_rule
from raidar.engine.trial.workspace import (
    SEED_SLUG,
    seed_check,
    seeded_digest,
    workspace_project,
    workspace_seed,
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


def _context(task_dir: Path) -> TrialContext:
    hashes = TrialHashes(
        lock="l",
        problem="p",
        arm="a",
        workflow="w",
        stage_code="s",
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
    name = tomllib.loads(workspace_project(ref, [], "3.12"))["project"]["name"]

    assert re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", name)
    assert name.endswith("-workspace")
    assert "@" not in name


def test_the_workspace_project_name_for_a_pinned_ref() -> None:
    document = tomllib.loads(workspace_project("scb/forge@38d627ecf668", [], "3.12"))

    assert document["project"]["name"] == "scb-forge-38d627ecf668-workspace"


def test_the_workspace_project_names_the_problem_python_and_declared_dependencies() -> None:
    document = tomllib.loads(workspace_project("scb/circuit_eval.v2", ["numpy>=2", "rich"], "3.12"))

    assert document["project"] == {
        "name": "scb-circuit-eval-v2-workspace",
        "version": "0",
        "requires-python": ">=3.12",
        "dependencies": ["numpy>=2", "rich"],
    }
    assert (
        tomllib.loads(workspace_project("scb/adder", [], "3.13"))["project"]["dependencies"] == []
    )


def test_the_seed_provision_carries_the_project_and_what_it_seeded() -> None:
    seed = workspace_seed("scb/adder", ["numpy"], "3.12")

    assert seed.slug == SEED_SLUG
    assert seed.snapshot == "/app"
    assert (
        seed.payload["pyproject.toml"].content
        == workspace_project("scb/adder", ["numpy"], "3.12").encode()
    )
    assert seed.facts["dependencies"] == ["numpy"]
    assert seed.facts["python"] == "3.12"
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
