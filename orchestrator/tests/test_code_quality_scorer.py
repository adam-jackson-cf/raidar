"""code-quality@3 behaviour over stored trials (plan G2/G3 acceptance).

A trial's per-step snapshots and verifier records go in; the Assessment holds
V/E/H per checkpoint and R/G per transition with the fork's values, every
unavailable value explicitly statused (never zero), and evidence that the
store validates against the stored snapshots (file, sha256, line spans).
"""

from __future__ import annotations

import dataclasses
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from raidar.core.snapshot import tree_digest
from raidar.packs.python.models import load_tool_lock
from raidar.packs.python.provisioner import ToolProvisioningError
from raidar.scorers.builtin_manifests import CODE_QUALITY
from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.code_quality import CodeQualityScorer
from raidar.scorers.code_quality.contract import EXPECTED_RUFF_VERSION
from raidar.scorers.code_quality.measurement import QUALITY_TOOL
from raidar.scorers.rescore import RescoreOutcome, assess
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Observation
from raidar.scorers.sdk.validation import validate_result
from raidar.scorers.store import AssessmentStore
from raidar.scorers.trial_inputs import load_trial_context
from raidar.verifier.models import CaseStatus, ParityVerdict
from raidar.verifier.records import (
    FORK_EVIDENCE_FILE,
    load_step_record,
    step_record_dir,
    write_step_record,
)
from tests.code_quality_support import case_trial, golden_case, quality_tools
from tests.scorer_support import StepFixture, tree_state, write_files, write_trial

SHOP_CONFIG = {"entry_file": "app.py"}
T12 = "checkpoint_1->checkpoint_2"
T23 = "checkpoint_2->checkpoint_3"


def _context(trial: Path, config: dict[str, Any] | None = None) -> AssessContext:
    ctx = load_trial_context(trial)
    ctx.config = dict(SHOP_CONFIG if config is None else config)
    return ctx


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


def test_unreached_and_unmeasurable_checkpoints_are_statused_and_block_their_transitions(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    trial = case_trial(tmp_path / "trial", case, reached=2, planned=["checkpoint_3"])
    write_files(trial / "steps" / "checkpoint_2" / "artifacts" / "app", {"shop/bad.py": b"\xff"})
    result = _scored(_rerecorded_context(trial))
    unreached = _observation(result, "verbosity", "checkpoint_3")
    assert (unreached.status, unreached.reason) == (
        AssessStatus.NOT_APPLICABLE,
        "checkpoint not reached",
    )
    invalid = _observation(result, "architecture", "checkpoint_2")
    assert invalid.status is AssessStatus.INELIGIBLE
    assert (invalid.reason or "").startswith("score_evidence_invalid: shop/bad.py")
    assert _observation(result, "rework", T12).status is AssessStatus.INELIGIBLE
    assert _observation(result, "rework", T23).status is AssessStatus.INELIGIBLE
    assert _observation(result, "verbosity", "checkpoint_1").status is AssessStatus.OK
    assert _observation(result, "problem_verbosity", "trajectory").status is (
        AssessStatus.INELIGIBLE
    )


def _rerecorded_context(trial: Path) -> AssessContext:
    """Context for a trial whose step-2 snapshot was edited before grading was recorded."""
    record_dir = step_record_dir(trial, "checkpoint_2")
    record = load_step_record(record_dir)
    snapshot = record.snapshot.model_copy(
        update={"digest": tree_digest(trial / record.snapshot.stored_path)}
    )
    write_step_record(record_dir, record.model_copy(update={"snapshot": snapshot}))
    return _context(trial)


def test_a_one_shot_problem_reports_rework_and_regression_as_not_applicable(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    result = _scored(_context(case_trial(tmp_path / "trial", case, reached=1)))
    assert not [item for item in result.observations if item.metric in {"rework", "regression"}]
    for metric in ("problem_rework", "problem_regression"):
        observation = _observation(result, metric, "trajectory")
        assert observation.status is AssessStatus.NOT_APPLICABLE
    assert _observation(result, "problem_architecture", "trajectory").status is AssessStatus.OK


def test_architecture_is_unavailable_without_an_entry_file_and_other_components_still_score(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    result = _scored(_context(case_trial(tmp_path / "trial", case), config={}))
    architecture = _observation(result, "architecture", "checkpoint_1")
    assert architecture.status is AssessStatus.UNAVAILABLE
    assert "entry_file" in (architecture.reason or "")
    assert _observation(result, "verbosity", "checkpoint_1").status is AssessStatus.OK
    assert _observation(result, "rework", T12).status is AssessStatus.OK


def test_an_unavailable_tool_environment_leaves_every_metric_unavailable(tmp_path: Path) -> None:
    def broken() -> Any:
        raise ToolProvisioningError("provisioning scb-python-quality failed: offline")

    ctx = _context(case_trial(tmp_path / "trial", golden_case("synthetic_shop")))
    result = validate_result(CODE_QUALITY, ctx, CodeQualityScorer(tools_factory=broken).assess(ctx))
    assert result.status is AssessStatus.UNAVAILABLE
    assert {item.status for item in result.observations} == {AssessStatus.UNAVAILABLE}
    assert "offline" in (result.reason or "")


def test_the_quality_tool_pins_are_the_scb_contract_and_the_test_env_pins() -> None:
    packages = set(load_tool_lock().tool(QUALITY_TOOL).packages)
    assert f"ruff=={EXPECTED_RUFF_VERSION}" in packages
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    dev = set(project["project"]["optional-dependencies"]["dev"])
    assert packages - {f"ruff=={EXPECTED_RUFF_VERSION}"} <= dev


def _drop_record(trial: Path) -> None:
    (step_record_dir(trial, "checkpoint_2") / "record.json").unlink()


def _drop_run_evidence(trial: Path) -> None:
    (step_record_dir(trial, "checkpoint_2") / FORK_EVIDENCE_FILE).unlink()


def _corrupt_run_evidence(trial: Path) -> None:
    (step_record_dir(trial, "checkpoint_2") / FORK_EVIDENCE_FILE).write_text("{}")


@pytest.mark.parametrize(
    ("mutate", "status", "reason"),
    [
        (
            _drop_record,
            AssessStatus.UNAVAILABLE,
            "checkpoint_2: the oracle did not grade this step",
        ),
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


def test_rework_needs_verbosity_evidence_at_both_ends_while_regression_does_not(
    tmp_path: Path,
) -> None:
    trial = case_trial(tmp_path / "trial", golden_case("synthetic_shop"), reached=2)
    write_files(
        trial / "steps" / "checkpoint_1" / "artifacts" / "app", {"shop/bad.py": "def x(:\n"}
    )
    record_dir = step_record_dir(trial, "checkpoint_1")
    record = load_step_record(record_dir)
    snapshot = record.snapshot.model_copy(
        update={"digest": tree_digest(trial / record.snapshot.stored_path)}
    )
    write_step_record(record_dir, record.model_copy(update={"snapshot": snapshot}))
    result = _scored(_context(trial))
    verbosity = _observation(result, "verbosity", "checkpoint_1")
    assert verbosity.status is AssessStatus.INELIGIBLE
    assert verbosity.reason == "score_evidence_invalid: missing production file row"
    rework = _observation(result, "rework", T12)
    assert (rework.status, rework.reason) == (
        AssessStatus.INELIGIBLE,
        "canonical_provenance_unavailable: no V/E symbol evidence for checkpoint_1",
    )
    assert _observation(result, "regression", T12).status is AssessStatus.OK


def test_a_trajectory_with_nothing_measurable_is_statused_as_a_whole(tmp_path: Path) -> None:
    unreached = load_trial_context(
        case_trial(tmp_path / "unreached", golden_case("synthetic_shop"), reached=1)
    )
    unreached.steps = (dataclasses.replace(unreached.steps[0], reached=False, snapshot=None),)
    result = _scored(unreached)
    assert (result.status, result.reason) == (
        AssessStatus.UNAVAILABLE,
        "no stored snapshot to measure",
    )
    js_trial = tmp_path / "js"
    write_trial(
        js_trial,
        [StepFixture("checkpoint_1", {"web/app.js": "let x = 1;\n"}, (CaseStatus.PASSED,))],
    )
    result = _scored(_context(js_trial))
    assert result.status is AssessStatus.INELIGIBLE
    assert (result.reason or "").startswith("production_language_unsupported")


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


@pytest.mark.parametrize("entry_file", ["renamed_missing.py", "../app.py", "shop"])
def test_an_entry_file_absent_from_the_snapshot_makes_architecture_ineligible_never_best(
    tmp_path: Path, entry_file: str
) -> None:
    case = golden_case("synthetic_shop")
    result = _scored(_context(case_trial(tmp_path / "trial", case), {"entry_file": entry_file}))
    for checkpoint in case["checkpoints"]:
        architecture = _observation(result, "architecture", checkpoint["checkpoint"])
        assert (architecture.status, architecture.value) == (AssessStatus.INELIGIBLE, None)
        assert (architecture.reason or "").startswith("entry_file_absent: ")
    assert _observation(result, "verbosity", "checkpoint_1").status is AssessStatus.OK
    assert _observation(result, "problem_architecture", "trajectory").value is None


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
