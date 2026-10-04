"""Manipulation check: guided vs baseline process-compliance separation (G5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.analysis.manipulation import (
    DEFAULT_CHECKS,
    PROCESS_COMPLIANCE_REFS,
    Separation,
    load_arm_results,
    manipulation_check,
    permutation_p,
)
from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.process_checks.manifest import PROCESS_COMPLIANCE_V2
from raidar.scorers.rescore import assess
from raidar.scorers.sdk.manifest import Tier
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus, Observation
from raidar.scorers.store import AssessmentStore
from tests.trace_trial_support import (
    TracedStep,
    agent_step,
    codex_exec,
    codex_patch,
    traced_context,
    trajectory,
)

TESTS = "tests_run_before_finish"
PLAN = "plan_written"


def _result(tests: list[float | None], plan: list[float | None]) -> AssessResult:
    """A trajectory's process-compliance result; None is an undecided checkpoint."""
    observations = []
    for metric, values in ((TESTS, tests), (PLAN, plan)):
        for index, value in enumerate(values, 1):
            observations.append(
                Observation(
                    metric=metric,
                    subject=f"checkpoint_{index}",
                    status=AssessStatus.OK,
                    value=value,
                )
                if value is not None
                else Observation(
                    metric=metric,
                    subject=f"checkpoint_{index}",
                    status=AssessStatus.NOT_APPLICABLE,
                    reason="no source edit in the step",
                )
            )
    observations.append(
        Observation(
            metric="compliance_rate",
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.NOT_APPLICABLE,
            reason="fixture",
        )
    )
    measured = any(item.status is AssessStatus.OK for item in observations)
    return AssessResult(
        status=AssessStatus.OK if measured else AssessStatus.NOT_APPLICABLE,
        reason=None if measured else "fixture has no decided check",
        observations=tuple(observations),
    )


def test_a_guided_arm_that_complies_is_separated_from_baseline() -> None:
    guided = [_result([1.0, 1.0], [1.0, None]) for _ in range(5)]
    baseline = [_result([0.0, 1.0], [0.0, 0.0]) for _ in range(4)] + [
        _result([0.0, 0.0], [None, None])
    ]
    report = manipulation_check(
        {"guided": guided, "baseline": baseline}, guided="guided", baseline="baseline"
    )

    assert report.verdict is Separation.SEPARATED
    tests, plan = report.checks
    assert (tests.check, tests.verdict) == (TESTS, Separation.SEPARATED)
    assert tests.p_value == pytest.approx(1 / 252)
    assert tests.method == "exact-permutation"
    # Trials without a decided checkpoint are not units.
    assert (plan.guided.trials, plan.baseline.trials) == (5, 4)
    assert plan.verdict is Separation.SEPARATED


def test_arms_that_behave_alike_are_reported_not_separated() -> None:
    same = [_result([1.0, 0.0], [1.0, 0.0]) for _ in range(6)]
    reversed_arms = {
        "guided": [_result([0.0], [0.0]) for _ in range(5)],
        "baseline": [_result([1.0], [1.0]) for _ in range(5)],
    }
    alike = manipulation_check({"g": same, "b": same}, guided="g", baseline="b")
    assert alike.verdict is Separation.NOT_SEPARATED
    assert all(check.difference == 0 for check in alike.checks)
    wrong_way = manipulation_check(reversed_arms, guided="guided", baseline="baseline")
    assert wrong_way.verdict is Separation.NOT_SEPARATED


def test_too_little_data_is_reported_as_such() -> None:
    tiny = manipulation_check(
        {"g": [_result([1.0], [1.0])], "b": [_result([0.0], [0.0])]}, guided="g", baseline="b"
    )
    assert tiny.verdict is Separation.INSUFFICIENT_DATA
    assert tiny.checks[0].min_p_value == pytest.approx(0.5)
    # Ties count: with binary shares many relabellings are as extreme as the most extreme.
    tied = manipulation_check(
        {
            "g": [_result([1.0], [1.0]) for _ in range(5)],
            "b": [_result([1.0], [1.0]) for _ in range(2)]
            + [_result([0.0], [0.0]) for _ in range(3)],
        },
        guided="g",
        baseline="b",
    )
    assert tied.checks[0].min_p_value == pytest.approx(21 / 252)
    assert tied.checks[0].verdict is Separation.NOT_SEPARATED
    assert "tied shares" in tied.checks[0].reason
    undecided = manipulation_check(
        {"g": [_result([None], [None])], "b": [_result([0.0], [0.0])]},
        guided="g",
        baseline="b",
        checks=[TESTS],
    )
    assert undecided.checks[0].verdict is Separation.INSUFFICIENT_DATA
    with pytest.raises(ValueError, match="no process-compliance results"):
        manipulation_check({"g": []}, guided="g", baseline="b")
    with pytest.raises(ValueError, match="at least one check"):
        manipulation_check({"g": [], "b": []}, guided="g", baseline="b", checks=[])


def test_large_arms_use_a_deterministic_sampled_permutation() -> None:
    guided = [1.0] * 15 + [0.0] * 5
    baseline = [0.0] * 15 + [1.0] * 5
    first = permutation_p(guided, baseline)
    assert first == permutation_p(guided, baseline)
    p_value, min_p, method = first
    assert method == "sampled-permutation"
    assert p_value < 0.01
    assert min_p < p_value


def _trial_trace(tested: bool) -> dict:
    calls = [agent_step(1, codex_patch("e1", "app/main.py"))]
    if tested:
        calls.append(agent_step(2, codex_exec("t1", "pytest")))
    return trajectory(*calls)


def test_default_checks_are_deterministic_metrics_of_the_default_scorer() -> None:
    # Jev parked (2026-10-04): the defaults read only deterministic process-compliance@2
    # metrics; an undetermined check is never ``ok``, so it never enters a share.
    metrics = {metric.id: metric for metric in PROCESS_COMPLIANCE_V2.metrics}
    assert set(DEFAULT_CHECKS) <= set(metrics)
    assert {metrics[check].tier for check in DEFAULT_CHECKS} == {Tier.DETERMINISTIC}
    assert PROCESS_COMPLIANCE_REFS[0] == PROCESS_COMPLIANCE_V2.ref


@pytest.mark.parametrize("ref", ["process-compliance@2", "process-compliance@1"])
def test_arm_results_load_from_the_assessment_store(tmp_path: Path, ref: str) -> None:
    registry = default_scorer_registry()
    store = AssessmentStore(tmp_path / "store")
    keys: dict[str, list[str]] = {"guided": [], "baseline": []}
    for arm, tested in (("guided", True), ("baseline", False)):
        for index in range(3):
            ctx = traced_context(
                tmp_path / f"{arm}-{index}",
                [TracedStep("checkpoint_1", trace=_trial_trace(tested))],
            )
            record = assess(store, registry.get(ref), ctx, trial=f"{arm}-{index}")
            keys[arm].append(record.key)
    results = load_arm_results(store.root, keys)
    report = manipulation_check(results, guided="guided", baseline="baseline", checks=[TESTS])
    assert report.checks[0].difference == 1.0
    assert report.checks[0].p_value == pytest.approx(1 / 20)

    other = assess(
        store,
        registry.get("correctness@1"),
        traced_context(tmp_path / "c", [TracedStep("checkpoint_1", trace=_trial_trace(True))]),
        trial="c",
    )
    with pytest.raises(ValueError, match="not one of process-compliance@2"):
        load_arm_results(store.root, {"guided": [other.key]})
    with pytest.raises(ValueError, match="no assessment"):
        load_arm_results(store.root, {"guided": ["0" * 64]})
