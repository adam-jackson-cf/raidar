"""Slow-tier behaviours split from :mod:`tests.test_grader_identity_and_canary`."""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.grading.grader import grade_step
from tests.dependency_support import FakeResolver
from tests.test_grader_identity_and_canary import (
    HangingSandbox as HangingSandbox,
)
from tests.test_grader_identity_and_canary import (
    bundle_fixture as bundle_fixture,
)
from tests.test_submission_dependencies import _declaring, _grading_bundle
from tests.verifier_support import write_app

pytestmark = pytest.mark.slow


async def test_a_timeout_the_starter_does_not_reproduce_is_a_failed_agent_outcome(
    tmp_path: Path, bundle
) -> None:
    app = write_app(tmp_path / "app")
    (app / "hang").write_text("")

    result = await grade_step(
        bundle, "checkpoint_1", app, HangingSandbox(tmp_path / "sb"), tmp_path / "w", coverage=True
    )

    assert result.canary.status == "passed"
    assert result.canary.trigger == "sandbox_timeout"
    assert result.grade.infra_failure is None
    assert not result.grade.solved
    assert result.grade.cases
    assert all(case.status.value == "failed" for case in result.grade.cases)
    assert result.grade.rewards()["infrastructure_failure"] == 0.0
    assert result.parity is not None
    assert not result.parity.eligible
    assert result.coverage is None


async def test_the_canary_grades_the_starter_in_the_submissions_sandbox_configuration(
    tmp_path: Path, bundle
) -> None:
    sandbox = HangingSandbox(tmp_path / "sb")
    app = write_app(tmp_path / "app")
    (app / "hang").write_text("")

    await grade_step(
        bundle, "checkpoint_1", app, sandbox, tmp_path / "w", coverage=False, timeout_s=123.0
    )

    graded, canary = sandbox.runs
    assert canary.timeout_s == graded.timeout_s == 123.0
    assert (canary.cpus, canary.memory_mb, canary.argv, canary.image) == (
        graded.cpus,
        graded.memory_mb,
        graded.argv,
        graded.image,
    )
    assert not any(canary.app_dir.iterdir())


async def test_a_graded_submission_runs_no_canary(tmp_path: Path, bundle) -> None:
    sandbox = HangingSandbox(tmp_path / "sb")
    result = await grade_step(
        bundle, "checkpoint_1", write_app(tmp_path / "app"), sandbox, tmp_path / "w", coverage=False
    )
    assert result.canary.status == "not_run"
    assert len(sandbox.runs) == 1


@pytest.mark.parametrize(
    ("marker", "trigger"),
    [("crash", "sandbox_error"), ("corrupt", "ledger_unreadable")],
)
async def test_a_crash_or_corrupted_ledger_the_starter_does_not_reproduce_is_a_failed_outcome(
    tmp_path: Path, bundle, marker: str, trigger: str
) -> None:
    app = write_app(tmp_path / "app")
    (app / marker).write_text("")

    result = await grade_step(
        bundle, "checkpoint_1", app, HangingSandbox(tmp_path / "sb"), tmp_path / "w", coverage=False
    )

    assert (result.canary.status, result.canary.trigger) == ("passed", trigger)
    assert result.grade.infra_failure is None
    assert result.grade.cases
    assert not result.grade.solved


async def test_the_canary_runs_on_the_submissions_dependency_image_and_closure(
    tmp_path: Path,
) -> None:
    fake = FakeResolver()
    bundle = _grading_bundle(tmp_path / "problem", fake)
    sandbox = HangingSandbox(tmp_path / "sandbox")
    app = _declaring(tmp_path / "app", "flask")
    (app / "hang").write_text("")

    result = await grade_step(
        bundle, "checkpoint_1", app, sandbox, tmp_path / "work", coverage=False, resolver=fake
    )

    graded, canary = sandbox.runs
    assert result.canary.status == "passed"
    assert canary.image == graded.image == "local-closure"
    assert canary.env["RAIDAR_SUBMISSION_DEPS"] == graded.env["RAIDAR_SUBMISSION_DEPS"]
    assert canary.env["PYTHONPATH"] == graded.env["PYTHONPATH"]
    assert len(set(sandbox.closures)) == 1
    # The starter declares nothing: the closure came from the submission's recorded dependencies.
    assert not any(canary.app_dir.iterdir())
