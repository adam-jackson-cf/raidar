"""Slow-tier behaviours split from :mod:`tests.test_grader_identity_and_canary`."""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.contract.wheels import DependencyInfraError
from raidar.grading.grader import grade_step
from raidar.grading.sandbox import LEDGER_NAME, SandboxResult, SandboxRun
from tests.dependency_support import FakeResolver
from tests.test_submission_dependencies import _declaring, _grading_bundle
from tests.verifier_support import LocalProcessSandbox, make_bundle, write_app

pytestmark = pytest.mark.slow


class HangingSandbox(LocalProcessSandbox):
    """A real local grader that misbehaves on runs staged with a marker file (the submission).

    ``hang``: the run times out. ``crash``: it exits without writing a ledger. ``corrupt``: the
    real run completes, then the ledger is truncated. ``always`` times out every run.
    """

    always: bool = False

    async def run(self, run: SandboxRun) -> SandboxResult:
        marked = {name for name in ("hang", "crash", "corrupt") if (run.app_dir / name).exists()}
        if self.always or "hang" in marked:
            self.runs.append(run)
            return SandboxResult(None, True, "")
        if "crash" in marked:
            self.runs.append(run)
            return SandboxResult(139, False, "segfault")
        result = await super().run(run)
        if "corrupt" in marked:
            (run.out_dir / LEDGER_NAME).write_text("{")
        return result


@pytest.fixture(name="bundle")
def bundle_fixture(tmp_path: Path):
    return make_bundle(tmp_path / "fixture")


async def test_a_sandbox_that_cannot_grade_the_starter_is_infrastructure(
    tmp_path: Path, bundle
) -> None:
    sandbox = HangingSandbox(tmp_path / "sb")
    sandbox.always = True
    app = write_app(tmp_path / "app")

    result = await grade_step(bundle, "checkpoint_1", app, sandbox, tmp_path / "w", coverage=False)

    assert result.canary.status == "failed"
    assert result.canary.detail.startswith("sandbox_timeout")
    assert result.grade.infra_failure is not None
    assert result.grade.infra_failure.code == "sandbox_timeout"
    assert result.grade.rewards() == {"infrastructure_failure": 1.0}


async def test_a_resolver_outage_is_never_attributed_to_the_submission(tmp_path: Path) -> None:
    fake = FakeResolver()
    bundle = _grading_bundle(tmp_path / "problem", fake)
    fake.error = DependencyInfraError("pypi unreachable")
    sandbox = HangingSandbox(tmp_path / "sandbox")

    result = await grade_step(
        bundle,
        "checkpoint_1",
        _declaring(tmp_path / "app", "flask"),
        sandbox,
        tmp_path / "work",
        coverage=False,
        resolver=fake,
    )

    assert result.grade.infra_failure is not None
    assert result.grade.infra_failure.code == "dependency_resolver_unavailable"
    assert result.canary.status == "not_run"
    assert sandbox.runs == []


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


async def test_a_graded_submission_runs_no_canary(tmp_path: Path, bundle) -> None:
    sandbox = HangingSandbox(tmp_path / "sb")
    result = await grade_step(
        bundle, "checkpoint_1", write_app(tmp_path / "app"), sandbox, tmp_path / "w", coverage=False
    )
    assert result.canary.status == "not_run"
    assert len(sandbox.runs) == 1
    assert result.grade.solved
    assert result.grade.infra_failure is None
    assert result.grade.rewards()["solved"] == 1.0


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
