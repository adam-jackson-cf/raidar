"""Grader identity pinning, the starter canary and who owns a grading crash."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.contract.wheels import DependencyInfraError
from raidar.design.problem.validation import (
    GraderIdentity,
    read_grader_identity,
    read_validation_record,
)
from raidar.engine.trial.plan import PlanError, _pinned_grader
from raidar.grading.grader import grade_step
from raidar.grading.grader_image import GraderIdentityError, refuse_grader_mismatch
from raidar.grading.records import grading_execution_digest
from raidar.grading.sandbox import LEDGER_NAME, SandboxResult, SandboxRun
from tests.dependency_support import FakeResolver
from tests.test_submission_dependencies import _declaring, _grading_bundle
from tests.verifier_support import LocalProcessSandbox, make_bundle, write_app

ENVIRONMENT = "sha256:" + "a" * 64
IMPLEMENTATION = "b" * 64
COMPLETE = {
    "grader_image": "raidar-oracle-grader:abc",
    "grader_environment_id": ENVIRONMENT,
    "grader_implementation_sha256": IMPLEMENTATION,
}


# ------------------------------------------------------------ recorded identity
def test_a_report_with_every_part_records_a_complete_identity() -> None:
    identity = read_grader_identity(COMPLETE)
    assert identity == GraderIdentity("raidar-oracle-grader:abc", ENVIRONMENT, IMPLEMENTATION)
    assert identity is not None
    assert identity.complete


def test_a_report_without_an_implementation_leaves_it_unmeasured() -> None:
    environment = {k: v for k, v in COMPLETE.items() if k != "grader_implementation_sha256"}
    identity = read_grader_identity(environment)
    assert identity == GraderIdentity("raidar-oracle-grader:abc", ENVIRONMENT, None)
    assert identity is not None
    assert not identity.complete
    assert identity.lock()["implementation_sha256"] is None


@pytest.mark.parametrize(
    "environment",
    [None, {}, {"grader_image": "g:1"}, {**COMPLETE, "grader_environment_id": "g:1"}],
)
def test_a_report_without_an_immutable_environment_id_pins_no_grader(environment: object) -> None:
    assert read_grader_identity(environment) is None


def test_a_malformed_implementation_digest_is_unmeasured_never_trusted() -> None:
    identity = read_grader_identity({**COMPLETE, "grader_implementation_sha256": "not-a-digest"})
    assert identity is not None
    assert identity.implementation_sha256 is None


def test_the_lock_pins_the_validated_grader_identity(tmp_path: Path) -> None:
    base = {"verdict": "validated", "manifest_sha256": "a" * 64, "not_run": []}
    path = tmp_path / "r.json"
    path.write_text(json.dumps({**base, "environment": COMPLETE}))
    pinned = read_validation_record(path).lock_metadata()["grader"]
    assert pinned == {
        "image": "raidar-oracle-grader:abc",
        "environment_id": ENVIRONMENT,
        "implementation_sha256": IMPLEMENTATION,
    }
    path.write_text(json.dumps(base))
    assert read_validation_record(path).lock_metadata()["grader"] is None


# ------------------------------------------------------------------ run refusal
def _references(grader: object) -> dict[str, object]:
    return {"fixture/adder@1": {"metadata": {"validation": {"grader": grader}}}}


def test_a_run_is_refused_when_the_lock_pins_no_complete_grader_identity() -> None:
    complete = {
        "image": "g:1",
        "environment_id": ENVIRONMENT,
        "implementation_sha256": grading_execution_digest(),
    }
    for missing in (None, {**complete, "implementation_sha256": None}, {"image": "g:1"}):
        with pytest.raises(PlanError, match="no complete grader identity"):
            _pinned_grader(0, "fixture/adder@1", _references(missing))
    assert _pinned_grader(0, "fixture/adder@1", _references(complete)) == complete


def test_a_run_is_refused_when_this_checkout_grades_with_another_implementation() -> None:
    other = {"image": "g:1", "environment_id": ENVIRONMENT, "implementation_sha256": "c" * 64}
    with pytest.raises(PlanError, match="validated under grading implementation"):
        _pinned_grader(0, "fixture/adder@1", _references(other))


def test_grading_refuses_a_grader_other_than_the_validated_one() -> None:
    pinned = {
        "image": "g:1",
        "environment_id": ENVIRONMENT,
        "implementation_sha256": IMPLEMENTATION,
    }
    refuse_grader_mismatch(
        pinned, image="g:1", environment_id=ENVIRONMENT, implementation_sha256=IMPLEMENTATION
    )
    for change in (
        {"image": "g:2"},
        {"environment_id": "sha256:" + "d" * 64},
        {"implementation_sha256": "e" * 64},
    ):
        found = {
            "image": "g:1",
            "environment_id": ENVIRONMENT,
            "implementation_sha256": IMPLEMENTATION,
            **change,
        }
        with pytest.raises(GraderIdentityError, match=next(iter(change))):
            refuse_grader_mismatch(pinned, **found)


# ----------------------------------------------------------------------- canary
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


@pytest.fixture
def bundle(tmp_path: Path):
    return make_bundle(tmp_path / "fixture")


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
