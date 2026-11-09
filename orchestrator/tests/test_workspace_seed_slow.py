"""Slow-tier behaviours split from :mod:`tests.test_workspace_seed`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from raidar.contract.trial import CheckpointState, PlannedTrial, TrialHashes
from raidar.design.experiment.schema import ProtocolSpec
from raidar.engine.trial.collect import TrialContext, classify_trial, execution_validity
from raidar.engine.trial.progression import progression_rule
from raidar.engine.trial.workspace import SEED_SLUG, seed_check, seeded_digest, workspace_seed
from tests.execution_support import (
    SEED_DIGEST,
    SEED_SHA,
    StepOutcome,
    write_seed_record,
    write_trial,
)
from tests.test_workspace_seed import (
    _LOCKS as _LOCKS,
)
from tests.test_workspace_seed import (
    CUTOFF as CUTOFF,
)

STEPS = ("checkpoint_1", "checkpoint_2")
RULE = progression_rule(ProtocolSpec())
PINNED = "0.156.1"

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("locked", [True, False])
def test_the_seed_install_script_is_valid_shell(locked: bool) -> None:
    seed = workspace_seed(
        "scb/adder", ["numpy"] if locked else [], "3.12", CUTOFF, _LOCKS if locked else {}
    )

    for script in (seed.install, seed.check):
        subprocess.run(["sh", "-n", "-c", script], check=True, timeout=120)


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
