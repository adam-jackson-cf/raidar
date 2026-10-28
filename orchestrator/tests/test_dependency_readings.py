"""Dependency readings: health ``D1``/``D2`` per step and the analysis' per-arm affected shares."""

from __future__ import annotations

from pathlib import Path

from raidar.engine.health.audit import audit_trial
from raidar.engine.health.dependencies import GRADER_LOG, local_modules, undeclared_modules
from raidar.engine.health.model import HealthCheck, HealthReport, write_health
from raidar.engine.trial.collect import HEALTH_VALIDITY
from raidar.evaluation.analysis.dependencies import (
    arm_dependencies,
    dependency_steps,
    dependency_warnings,
)
from raidar.evaluation.analysis.outcomes import DependencyStep, TrialOutcome
from raidar.grading.records import load_step_record, step_record_dir, write_step_record
from raidar.grading.submission_deps import SubmissionDependencies
from tests.health_support import STEP, healthy_trial


def _dependencies(declared: tuple[str, ...], unprovided: list[dict[str, str]]) -> object:
    return SubmissionDependencies.model_validate(
        {
            "declared": declared,
            "set_sha256": "s" * 64,
            "source": "resolved",
            "lock_text": "",
            "lock_sha256": "l" * 64,
            "arch": "arm64",
            "provided": (),
            "unprovided": unprovided,
            "resolve_s": 0.0,
            "build_s": 0.0,
            "image_id": "sha256:x",
        }
    )


def _readings(
    tmp_path: Path, declared: tuple[str, ...], unprovided: list[dict[str, str]], log: str | None
) -> dict[str, HealthCheck]:
    t = healthy_trial(tmp_path)
    record_dir = step_record_dir(t.trial_dir, STEP)
    ledger = load_step_record(record_dir)
    deps = _dependencies(declared, unprovided)
    write_step_record(record_dir, ledger.model_copy(update={"submission_dependencies": deps}))
    if log is not None:
        (record_dir / GRADER_LOG).write_text(log)
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    return {c.id: c for c in report.checks}


def test_undeclared_modules_skip_stdlib_local_and_declared_names() -> None:
    log = (
        "ModuleNotFoundError: No module named 'numpy.core'\n"
        "ModuleNotFoundError: No module named 'os.pathx'\n"
        "ModuleNotFoundError: No module named 'my_pkg'\n"
        "ModuleNotFoundError: No module named 'mine'\n"
        "ModuleNotFoundError: No module named 'yaml'\n"
    )
    found = undeclared_modules(log, ["my-pkg>=1", "bad requirement !!"], {"mine"})
    assert found == [
        {"module": "numpy", "basis": "heuristic"},
        {"module": "yaml", "basis": "heuristic"},
    ]
    assert undeclared_modules(log, [], set())[0]["basis"] == "confirmed"
    assert undeclared_modules(log, [], None)[0]["basis"] == "heuristic"


def test_local_modules_read_the_snapshot_root_and_src(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tool.py").write_text("")
    (tmp_path / "src" / "lib").mkdir(parents=True)
    assert local_modules(tmp_path) == {"pkg", "tool", "src", "lib"}


def test_d1_and_d2_record_structured_evidence_per_step(tmp_path: Path) -> None:
    unprovided = [{"requirement": "sdistonly==1", "reason": "no-wheel"}]
    log = "ModuleNotFoundError: No module named 'polars'\n"
    checks = _readings(tmp_path, ("sdistonly==1",), unprovided, log)
    assert checks["D1"].status == "pass"
    assert checks["D1"].evidence["steps"] == {STEP: unprovided}
    assert checks["D2"].evidence["steps"] == {STEP: [{"module": "polars", "basis": "heuristic"}]}
    assert checks["D2"].evidence["unmeasured"] == []


def test_a_missing_grader_log_is_unmeasured_not_unaffected(tmp_path: Path) -> None:
    checks = _readings(tmp_path, (), [], None)
    assert checks["D1"].evidence["steps"] == {STEP: []}
    assert checks["D2"].status == "unmeasured"
    assert checks["D2"].evidence["unmeasured"] == [STEP]


def test_readings_never_invalidate_a_trial() -> None:
    grouped = {check for ids in HEALTH_VALIDITY.values() for check in ids}
    assert not grouped & {"D1", "D2"}


def _outcome(arm: str, steps: list[DependencyStep]) -> TrialOutcome:
    return TrialOutcome(
        trial_id=f"{arm}-t",
        job_id="j",
        arm=arm,
        attempt=0,
        problem="p@1",
        steps=("a", "b"),
        infra_error=False,
        dependency_steps=tuple(steps),
    )


MISSING_PKG = ({"requirement": "x", "reason": "no-wheel"},)
GUESS = ({"module": "m", "basis": "heuristic"},)


def test_arm_shares_count_unmeasured_apart_and_solvable_failures() -> None:
    steps = [
        DependencyStep(
            step="a", unprovided=MISSING_PKG, undeclared_imports=GUESS, solvable_solved=False
        ),
        DependencyStep(step="b", unprovided=(), undeclared_imports=None, solvable_solved=True),
    ]
    shares = arm_dependencies([_outcome("x", steps)]) or {}
    unprovided = shares["unprovided"]
    assert (unprovided.measured, unprovided.affected, unprovided.share) == (2, 1, 0.5)
    assert (unprovided.solvable_failed, unprovided.solvable_judged) == (1, 1)
    imports = shares["undeclared_import"]
    assert (imports.measured, imports.unmeasured, imports.share) == (1, 1, 1.0)
    assert imports.heuristic == 1


def test_no_measured_step_is_no_share_never_zero() -> None:
    shares = arm_dependencies([_outcome("x", [DependencyStep(step="a")])]) or {}
    assert shares["unprovided"].share is None
    assert arm_dependencies([_outcome("x", [])]) is None


def test_warning_when_arms_differ_materially_or_are_not_comparable() -> None:
    hit = [DependencyStep(step="a", unprovided=MISSING_PKG, undeclared_imports=())]
    clean = [DependencyStep(step="a", unprovided=(), undeclared_imports=())]
    arms = {
        "base": arm_dependencies([_outcome("base", clean)]),
        "t": arm_dependencies([_outcome("t", hit)]),
    }
    warnings = dependency_warnings("base", arms, ["t"])
    assert len(warnings) == 1
    assert "unprovided" in warnings[0]
    assert "inspect the affected steps" in warnings[0]
    assert "confound" not in warnings[0]
    arms["t"] = None
    assert all("not comparable" in w for w in dependency_warnings("base", arms, ["t"]))
    arms["t"] = arms["base"]
    assert dependency_warnings("base", arms, ["t"]) == ()


def test_steps_without_readings_are_unmeasured(tmp_path: Path) -> None:
    found = dependency_steps(("a", "b"), tmp_path, None)
    assert [(s.step, s.unprovided, s.solvable_solved) for s in found] == [
        ("a", None, None),
        ("b", None, None),
    ]
    old = HealthReport(trial_id="t", checks=())
    write_health(tmp_path, old)
    assert [s.step for s in dependency_steps(("a", "b"), tmp_path, None)] == ["a", "b"]
