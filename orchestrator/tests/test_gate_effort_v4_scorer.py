"""gate-effort@4: gate-effort@3 plus the gate's tests check and the fix rounds' correctness.

- every @3 observation is kept, except that ``TEST.*`` findings count in the
  ``tests`` family (not ``other``);
- the tests check outcomes come from each run's report (``tests`` block);
- ``correctness_delta`` is the step's final case pass rate minus the pass rate
  of the workspace its first fix session started from, graded offline into the
  store; ungraded is ``unavailable`` and the Assessment provisional; a shadow
  gate or a step without a fix round is ``not_applicable``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.gate_effort.records import RECORD_PATH
from raidar.scorers.gate_effort_v3 import GATE_EFFORT_V3, gate_effort_v3_scorer
from raidar.scorers.gate_effort_v4 import (
    GATE_EFFORT_V4,
    gate_effort_v4_scorer,
    gate_effort_v4_scorer_for_store,
)
from raidar.scorers.gate_effort_v4.scorer import NO_FIX_ROUND, family_v4
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus
from raidar.scorers.sdk.validation import validate_result
from raidar.verifier.models import CaseGroup, CaseResult, CaseStatus, VerifierGrade
from raidar.verifier.prefix_store import GRADE_KIND, grade_path
from raidar.verifier.records import (
    BundleRef,
    CoverageRef,
    GraderRef,
    SnapshotRef,
    StepRecord,
)
from tests.test_gate_effort_scorer import (
    PASSED,
    REFUSED,
    _finding,
    _step,
    _trace,
    _value,
    _values,
    _write_gate,
)
from tests.test_gate_effort_v2_scorer import _shadow

CP1, CP2 = "checkpoint_1", "checkpoint_2"
BUNDLE = "b" * 64
PRE_FIX = "p" * 64


def _grade(step: str, passed: int, failed: int) -> VerifierGrade:
    statuses = [CaseStatus.PASSED] * passed + [CaseStatus.FAILED] * failed
    cases = tuple(
        CaseResult(id=f"c{i}", group=CaseGroup.CORE, checkpoint_origin=step, status=status)
        for i, status in enumerate(statuses)
    )
    return VerifierGrade(step=step, checkpoint=step, cases=cases)


def _graded(step: StepInputs, passed: int, failed: int) -> StepInputs:
    grade = _grade(step.step, passed, failed)
    record = StepRecord(
        step=step.step,
        checkpoint=step.step,
        bundle=BundleRef(root="/b", digest=BUNDLE, problem="toy"),
        snapshot=SnapshotRef(requested="artifact", used="artifact", digest="s", stored_path="x"),
        grader=GraderRef(image="g", environment_id="sha256:e", image_built=False, timeout_s=60),
        grade=grade,
        summary=grade.summary(),
        coverage=CoverageRef(enabled=False),
        rewards=grade.rewards(),
    )
    return dataclasses.replace(step, record=record, record_digest="r")


def _tests(passed: bool, *, coverage: float | None = 90.0, ran: int = 3) -> dict:
    return {"required": True, "tests_ran": ran, "coverage_percent": coverage, "runners": {}}


def _report(trace: Path, run: int, findings: list[dict], tests: dict | None) -> None:
    folder = trace / "guardrail-gate" / f"gate_run_{run}"
    folder.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "python-guardrails.gate-report/1",
        "verdict": "refused" if findings else "passed",
        "findings": findings,
    }
    if tests is not None:
        report["tests"] = tests
    (folder / "gate-1.json").write_text(json.dumps(report))


def _pre_fix(trace: Path, digest: str | None = PRE_FIX) -> None:
    record = json.loads((trace / RECORD_PATH).read_text())
    record["pre_fix_snapshots"] = [
        {"round": 1, "before_session": "stage_2_fix", "path": "p", "tree_digest": digest}
    ]
    (trace / RECORD_PATH).write_text(json.dumps(record))


def _store_grade(store: Path, step: str, passed: int, failed: int) -> None:
    path = grade_path(store, BUNDLE, step, PRE_FIX)
    path.parent.mkdir(parents=True, exist_ok=True)
    grade = _grade(step, passed, failed)
    payload = {
        "kind": GRADE_KIND,
        "step": step,
        "snapshot_digest": PRE_FIX,
        "grade": grade.model_dump(mode="json"),
    }
    path.write_text(json.dumps(payload))


def _assess(*steps: StepInputs, store: Path | None = None) -> tuple[AssessResult, bool]:
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    scorer = gate_effort_v4_scorer_for_store(store) if store else gate_effort_v4_scorer()
    result = validate_result(GATE_EFFORT_V4, ctx, scorer.assess(ctx))
    return result, scorer.awaiting_answers()


def _enforced(tmp_path: Path) -> tuple[StepInputs, StepInputs]:
    """cp1: refused for missing tests, fixed (a fix round); cp2: clean first time."""
    first = _graded(_step(tmp_path, CP1), passed=3, failed=1)
    _write_gate(_trace(first), outputs=[REFUSED, PASSED], termination="gate_clean", signal=True)
    _report(
        _trace(first),
        1,
        [_finding("TEST.missing"), _finding("C901")],
        _tests(False, coverage=None, ran=0),
    )
    _report(_trace(first), 2, [], _tests(True, coverage=92.5))
    _pre_fix(_trace(first))
    second = _graded(_step(tmp_path, CP2), passed=4, failed=0)
    _write_gate(_trace(second), outputs=[PASSED], termination="gate_clean", signal=True)
    _report(_trace(second), 1, [], _tests(True, coverage=88.0))
    return first, second


def test_test_codes_are_the_tests_family() -> None:
    assert family_v4("TEST.missing") == "tests"
    assert family_v4("TEST.coverage-minimum") == "tests"
    assert family_v4("C901") == "complexity"


def test_the_tests_check_and_the_fix_rounds_correctness_are_measured(tmp_path: Path) -> None:
    store = tmp_path / "store"
    _store_grade(store, CP1, passed=4, failed=0)
    first, second = _enforced(tmp_path)

    result, awaiting = _assess(first, second, store=store)

    assert not awaiting
    assert _value(result, "first_run_blocking.tests", CP1) == 1
    assert _value(result, "first_run_blocking.other", CP1) == 0
    assert _value(result, "first_run_tests_passed", CP1) == 0
    assert _value(result, "first_run_tests_missing", CP1) == 1
    assert _values(result)[("first_run_coverage", CP1)].status is AssessStatus.NOT_APPLICABLE
    assert (
        _value(result, "tests_passed_at_step_end", CP1),
        _value(result, "coverage_at_step_end", CP1),
    ) == (1, 92.5)
    assert _value(result, "pre_fix_pass_rate", CP1) == 1.0
    assert _value(result, "correctness_delta", CP1) == pytest.approx(-0.25)
    gap = _values(result)[("correctness_delta", CP2)]
    assert (gap.status, gap.reason) == (AssessStatus.NOT_APPLICABLE, NO_FIX_ROUND)
    delta = _values(result)[("fix_round_correctness_delta", TRAJECTORY_SUBJECT)]
    assert (delta.value, delta.denominator) == (pytest.approx(-0.25), 1.0)
    assert _value(result, "tests_missing_rate", TRAJECTORY_SUBJECT) == 0.5
    assert _value(result, "first_run_tests_pass_rate", TRAJECTORY_SUBJECT) == 0.5
    assert _value(result, "mean_coverage_at_step_end", TRAJECTORY_SUBJECT) == pytest.approx(90.25)


def test_an_ungraded_pre_fix_snapshot_is_unavailable_and_the_assessment_provisional(
    tmp_path: Path,
) -> None:
    first, second = _enforced(tmp_path)

    result, awaiting = _assess(first, second, store=tmp_path / "empty-store")

    assert awaiting
    item = _values(result)[("correctness_delta", CP1)]
    assert item.status is AssessStatus.UNAVAILABLE
    assert "not graded yet" in (item.reason or "")
    total = _values(result)[("fix_round_correctness_delta", TRAJECTORY_SUBJECT)]
    assert total.status is AssessStatus.UNAVAILABLE
    assert not _assess(first, second)[1], "without a store nothing is awaited"


def test_every_v3_observation_is_kept_except_the_tests_family(tmp_path: Path) -> None:
    first, second = _enforced(tmp_path)
    ctx = AssessContext(problem="scb/forge", steps=(first, second), config={})
    v3 = _values(validate_result(GATE_EFFORT_V3, ctx, gate_effort_v3_scorer().assess(ctx)))
    v4 = _values(_assess(first, second)[0])

    assert set(v3) <= set(v4)
    differ = {key for key in v3 if v3[key] != v4[key]}
    assert {metric for metric, _ in differ} == {
        "first_run_blocking.other",
        "first_run_blocking.tests",
        "blocking_findings.other",
        "blocking_findings.tests",
        "total_first_run_blocking.other",
        "total_first_run_blocking.tests",
        "total_blocking_findings.other",
        "total_blocking_findings.tests",
    }


def test_a_shadow_gate_measures_the_code_not_the_fix_rounds(tmp_path: Path) -> None:
    step = _graded(_step(tmp_path, CP1), passed=2, failed=2)
    _shadow(_trace(step), REFUSED, findings=[_finding("TEST.coverage-minimum")])
    record = json.loads((_trace(step) / RECORD_PATH).read_text())
    _report(_trace(step), 1, [_finding("TEST.coverage-minimum")], _tests(False, coverage=60.0))
    (_trace(step) / RECORD_PATH).write_text(json.dumps(record))

    result, _ = _assess(step)

    assert _value(result, "first_run_tests_passed", CP1) == 0
    assert _value(result, "coverage_at_step_end", CP1) == 60.0
    for metric in ("correctness_delta", "pre_fix_pass_rate", "fix_rounds"):
        assert _values(result)[(metric, CP1)].status is AssessStatus.NOT_APPLICABLE
    total = _values(result)[("fix_round_correctness_delta", TRAJECTORY_SUBJECT)]
    assert total.status is AssessStatus.NOT_APPLICABLE


def test_a_pack_without_a_tests_check_leaves_its_outcomes_not_applicable(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    _write_gate(_trace(step), outputs=[PASSED], termination="gate_clean", signal=True)

    result, _ = _assess(step)

    item = _values(result)[("first_run_tests_passed", CP1)]
    assert item.status is AssessStatus.NOT_APPLICABLE
    assert _value(result, "first_pass_clean", CP1) == 1


def test_v4_is_registered_store_bound_with_v3s_metrics_and_more() -> None:
    registration = default_scorer_registry().get("gate-effort@4")
    v3 = {metric.id for metric in default_scorer_registry().get("gate-effort@3").manifest.metrics}

    assert registration.store_implementation is not None
    assert v3 < {metric.id for metric in registration.manifest.metrics}
    assert registration.jev_policy_bindings() is None
