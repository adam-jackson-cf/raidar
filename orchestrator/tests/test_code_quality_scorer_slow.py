"""code-quality@9 over stored trials with the real pinned tools (plan G2/G3 acceptance).

A trial's per-step snapshots and verifier records go in; the Assessment holds the fork's
V/E/H/R/G values with traceable evidence the store validates against the stored snapshots,
and what only a stored trial decides (oracle records, run evidence, planned steps) is
statused. The scoring decisions themselves are fast tests over in-memory trajectories.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from raidar.grading.models import ParityVerdict
from raidar.grading.records import FORK_EVIDENCE_FILE, step_record_dir
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.code_quality import CodeQualityScorer
from raidar.scoring.framework.builtin_manifests import CODE_QUALITY
from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.rescore import RescoreOutcome, assess
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation
from raidar.scoring.framework.store import AssessmentStore
from raidar.scoring.framework.validation import validate_result
from tests.code_quality_support import case_trial, golden_case, quality_tools
from tests.code_quality_support import trial_context as _context
from tests.scorer_support import tree_state

pytestmark = pytest.mark.slow

T12 = "checkpoint_1->checkpoint_2"
T23 = "checkpoint_2->checkpoint_3"


def _scored(ctx: AssessContext) -> AssessResult:
    scorer = CodeQualityScorer(tools_factory=quality_tools)
    return validate_result(CODE_QUALITY, ctx, scorer.assess(ctx))


def _observation(result: AssessResult, metric: str, subject: str) -> Observation:
    return next(
        item for item in result.observations if item.metric == metric and item.subject == subject
    )


def _value(decimal_text: str) -> float:
    return float(Decimal(decimal_text))


def test_every_checkpoint_and_transition_carries_the_fork_values_and_traceable_evidence(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    trial = case_trial(tmp_path / "trial", case)
    before = tree_state(trial)
    store = AssessmentStore(tmp_path / "store")
    registration = default_scorer_registry().get(CODE_QUALITY.ref)
    record = assess(store, registration, _context(trial), trial="trial")
    assert tree_state(trial) == before
    assessment = store.get(record.key)
    assert assessment is not None
    result = assessment.result
    assert result.status is AssessStatus.OK
    for checkpoint in case["checkpoints"]:
        step, production = checkpoint["checkpoint"], checkpoint["production"]
        quality, graph = production["production_quality"], production["production_graph"]
        assert _observation(result, "verbosity", step).value == _value(quality["verbosity"])
        assert _observation(result, "erosion", step).value == _value(quality["erosion"])
        assert _observation(result, "architecture", step).value == _value(graph["architecture"])
        assert (
            _observation(result, "production_loc", step).value
            == (quality["source_loc_denominator"])
        )
        assert _observation(result, "lint_findings", step).value == len(
            quality["ruff"]["diagnostics"]
        )
    reworks = [_value(item["rework"]) for item in case["lineage"]["transitions"]]
    assert [_observation(result, "rework", key).value for key in (T12, T23)] == reworks
    assert _observation(result, "regression", T12).value == _value(
        case["regressions"][T12]["regression"]
    )
    parity_failure = _observation(result, "regression", T23)
    assert parity_failure.status is AssessStatus.INELIGIBLE
    assert parity_failure.value is None
    assert parity_failure.reason == "coverage_parity_unavailable"
    kinds = {(item.metric, item.kind) for item in result.evidence}
    assert {
        ("verbosity", "ruff:SIM108"),
        ("verbosity", "clone"),
        ("lint_findings", "ruff:SIM108"),
        ("erosion", "high_complexity"),
        ("architecture", "cyclic_call"),
        ("rework", "repeated_rework:added"),
        ("regression", "regression_attribution"),
    } <= kinds
    assert all(item.locations() for item in result.evidence)
    replayed = assess(store, registration, _context(trial), trial="trial", replay=True)
    assert replayed.outcome is RescoreOutcome.REPLAYED_IDENTICAL


def test_problem_level_components_are_means_only_when_every_subject_has_a_value(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    result = _scored(_context(case_trial(tmp_path / "trial", case)))
    verbosity = [
        Decimal(item["production"]["production_quality"]["verbosity"])
        for item in case["checkpoints"]
    ]
    mean = _observation(result, "problem_verbosity", "trajectory")
    assert mean.value == float(sum(verbosity) / len(verbosity))
    assert _observation(result, "problem_rework", "trajectory").status is AssessStatus.OK
    regression = _observation(result, "problem_regression", "trajectory")
    assert regression.status is AssessStatus.INELIGIBLE
    assert regression.value is None
    assert T23 in (regression.reason or "")


def test_regression_without_coverage_parity_is_ineligible_with_reasons_never_zero(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    parity = ParityVerdict(eligible=False, reasons=("outcome_mismatch",))
    trial = case_trial(tmp_path / "trial", case, parity=parity)
    result = _scored(_context(trial))
    for key in (T12, T23):
        observation = _observation(result, "regression", key)
        assert (observation.status, observation.value, observation.reason) == (
            AssessStatus.INELIGIBLE,
            None,
            "coverage_parity_failed: outcome_mismatch",
        )
    assert _observation(result, "rework", T12).status is AssessStatus.OK


def _drop_run_evidence(trial: Path) -> None:
    (step_record_dir(trial, "checkpoint_2") / FORK_EVIDENCE_FILE).unlink()


def _corrupt_run_evidence(trial: Path) -> None:
    (step_record_dir(trial, "checkpoint_2") / FORK_EVIDENCE_FILE).write_text("{}")


@pytest.mark.parametrize(
    ("mutate", "status", "reason"),
    [
        (
            _drop_run_evidence,
            AssessStatus.INELIGIBLE,
            "coverage_parity_unavailable: g-kernel-input.json missing",
        ),
        (_corrupt_run_evidence, AssessStatus.INELIGIBLE, "coverage_parity_unavailable: 'baseline'"),
    ],
)
def test_regression_needs_the_oracle_record_and_its_run_evidence(
    tmp_path: Path, mutate: Any, status: AssessStatus, reason: str
) -> None:
    trial = case_trial(tmp_path / "trial", golden_case("synthetic_shop"), reached=2)
    mutate(trial)
    observation = _observation(_scored(_context(trial)), "regression", T12)
    assert (observation.status, observation.value, observation.reason) == (status, None, reason)


def test_unreached_checkpoints_leave_problem_means_unavailable_unlike_a_one_shot_problem(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    trial = case_trial(tmp_path / "trial", case, reached=2, planned=["checkpoint_3"])
    result = _scored(_context(trial))
    assert _observation(result, "rework", T23).status is AssessStatus.NOT_APPLICABLE
    for metric in ("problem_verbosity", "problem_architecture", "problem_rework"):
        observation = _observation(result, metric, "trajectory")
        assert (observation.status, observation.value) == (AssessStatus.UNAVAILABLE, None)
        assert "not_applicable" in (observation.reason or "")


def _rewrite_run_evidence(trial: Path) -> None:
    path = step_record_dir(trial, "checkpoint_2") / FORK_EVIDENCE_FILE
    path.write_text(path.read_text() + "\n")


@pytest.mark.parametrize("mutate", [_drop_run_evidence, _rewrite_run_evidence])
def test_the_g_run_evidence_is_part_of_the_assessment_key(tmp_path: Path, mutate: Any) -> None:
    trial = case_trial(tmp_path / "trial", golden_case("synthetic_shop"), reached=2)
    store = AssessmentStore(tmp_path / "store")
    registration = default_scorer_registry().get(CODE_QUALITY.ref)
    first = assess(store, registration, _context(trial), trial="trial")
    mutate(trial)
    second = assess(store, registration, _context(trial), trial="trial")
    assert second.key != first.key
    assert second.outcome is RescoreOutcome.ADDED
