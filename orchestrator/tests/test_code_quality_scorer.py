"""code-quality@9 behaviour over in-memory trajectories (plan G2/G3 acceptance).

Snapshots on disk and an in-memory tool environment go in; V/E/H per checkpoint and R/G per
transition come out with the fork's values, and every unavailable value is explicitly statused
(never zero). Stored trials, the real pinned tools and the Assessment store are the slow
file's seams.
"""

from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path
from typing import Any

import pytest

from raidar.scoring.code_quality import CodeQualityScorer
from raidar.scoring.code_quality import scorer as scorer_module
from raidar.scoring.code_quality.checkpoint import Gap, Measured, measure_checkpoint
from raidar.scoring.code_quality.contract import EXPECTED_RUFF_VERSION
from raidar.scoring.code_quality.measurement import QUALITY_TOOL
from raidar.scoring.code_quality.observations import EXCLUDED_METRIC
from raidar.scoring.code_quality.transition import measure_transitions
from raidar.scoring.framework.builtin_manifests import CODE_QUALITY
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation
from raidar.scoring.framework.validation import validate_result
from raidar.subject.environment.python.pack_definition import load_tool_lock
from raidar.subject.environment.python.provisioner import ToolProvisioningError
from tests.code_quality_support import (
    golden_case,
    golden_runs,
    in_memory_tools,
    memory_context,
)

T12 = "checkpoint_1->checkpoint_2"
T23 = "checkpoint_2->checkpoint_3"


def _scored(ctx: AssessContext) -> AssessResult:
    scorer = CodeQualityScorer(tools_factory=in_memory_tools)
    return validate_result(CODE_QUALITY, ctx, scorer.assess(ctx))


def _observation(result: AssessResult, metric: str, subject: str) -> Observation:
    return next(
        item for item in result.observations if item.metric == metric and item.subject == subject
    )


def test_an_unavailable_tool_environment_leaves_every_metric_unavailable(tmp_path: Path) -> None:
    def broken() -> Any:
        raise ToolProvisioningError("provisioning scb-python-quality failed: offline")

    ctx = memory_context(tmp_path, golden_case("synthetic_shop"))
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


class _GoldenReader:
    """The coverage reader with G's run evidence as the fork recorded it (no stored trial)."""

    def __init__(self, case: dict[str, Any]) -> None:
        self._runs = golden_runs(case)
        self.excluded: dict[str, tuple[str, ...]] = {}
        self.missing: list[str] = []

    def run_evidence(self, step: StepInputs) -> Any:
        found = self._runs(step)
        if not isinstance(found, Gap):
            self.excluded[step.step] = ()
        return found


def test_every_checkpoint_and_transition_carries_the_fork_values_and_traceable_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = golden_case("synthetic_shop")
    reader = _GoldenReader(case)
    monkeypatch.setattr(scorer_module, "CoverageReader", lambda store_root: reader)

    result = _scored(memory_context(tmp_path, case))

    assert result.status is AssessStatus.OK
    for checkpoint in case["checkpoints"]:
        step, production = checkpoint["checkpoint"], checkpoint["production"]
        quality, graph = production["production_quality"], production["production_graph"]
        assert _observation(result, "erosion", step).value == float(quality["erosion"])
        assert _observation(result, "architecture", step).value == float(graph["architecture"])
        assert (
            _observation(result, "production_loc", step).value
            == (quality["source_loc_denominator"])
        )
        # The in-memory Ruff finds nothing: V can only be at least the fork's.
        assert _observation(result, "lint_findings", step).value == 0
        assert _observation(result, "verbosity", step).value >= float(quality["verbosity"])
    reworks = [float(item["rework"]) for item in case["lineage"]["transitions"]]
    assert [_observation(result, "rework", key).value for key in (T12, T23)] == reworks
    assert _observation(result, "regression", T12).value == float(
        case["regressions"][T12]["regression"]
    )
    # The fork recorded no run evidence for the last step: G is a typed gap, never a value.
    missing = _observation(result, "regression", T23)
    assert (missing.status, missing.value) == (AssessStatus.INELIGIBLE, None)
    assert (missing.reason or "").startswith("coverage_parity_unavailable")
    assert _observation(result, EXCLUDED_METRIC, T12).value == 0.0
    assert _observation(result, "problem_regression", "trajectory").status is (
        AssessStatus.INELIGIBLE
    )
    kinds = {(item.metric, item.kind) for item in result.evidence}
    assert {
        ("verbosity", "clone"),
        ("erosion", "high_complexity"),
        ("architecture", "cyclic_call"),
        ("rework", "repeated_rework:added"),
        ("regression", "regression_attribution"),
    } <= kinds
    assert all(item.locations() for item in result.evidence)


def test_an_ungraded_step_blocks_regression_but_not_rework(tmp_path: Path) -> None:
    ctx = memory_context(tmp_path, golden_case("synthetic_shop"), reached=2)
    result = _scored(ctx)
    regression = _observation(result, "regression", T12)
    assert (regression.status, regression.value) == (AssessStatus.UNAVAILABLE, None)
    assert "did not grade" in (regression.reason or "")
    assert _observation(result, "rework", T12).status is AssessStatus.OK


def test_rework_needs_verbosity_evidence_at_both_ends_while_regression_does_not(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    first = {**case["checkpoints"][0]["files"], "shop/bad.py": "def x(:\n"}
    ctx = memory_context(tmp_path, case, reached=2, files={"checkpoint_1": first})
    tools = in_memory_tools()
    measures = [measure_checkpoint(ctx, step, tools, "app.py") for step in ctx.steps]

    (transition,) = measure_transitions(measures, golden_runs(case))

    quality = measures[0].quality()
    assert isinstance(quality, Gap)
    assert quality.reason.startswith("score_evidence_invalid")
    assert isinstance(transition.rework, Gap)
    assert transition.rework.status is AssessStatus.INELIGIBLE
    assert transition.rework.reason.startswith("canonical_provenance_unavailable")
    # G does not need V/E evidence: dropping it from the other end too leaves G's attribution
    # byte-identical.
    assert isinstance(measures[1].outcome, Measured)
    blind = dataclasses.replace(
        measures[1],
        outcome=dataclasses.replace(
            measures[1].outcome, quality=Gap(AssessStatus.INELIGIBLE, "no V/E")
        ),
    )
    (without_ve,) = measure_transitions([measures[0], blind], golden_runs(case))
    assert not isinstance(transition.regression, Gap)
    assert not isinstance(without_ve.regression, Gap)
    assert without_ve.regression.model_dump() == transition.regression.model_dump()


def test_unreached_and_unmeasurable_checkpoints_are_statused_and_block_their_transitions(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    second = {**case["checkpoints"][1]["files"], "shop/bad.py": b"\xff"}
    ctx = memory_context(tmp_path, case, reached=2, planned=True, files={"checkpoint_2": second})
    result = _scored(ctx)
    unreached = _observation(result, "verbosity", "checkpoint_3")
    assert unreached.status is AssessStatus.NOT_APPLICABLE
    invalid = _observation(result, "architecture", "checkpoint_2")
    assert invalid.status is AssessStatus.INELIGIBLE
    assert (invalid.reason or "").startswith("score_evidence_invalid: shop/bad.py")
    assert _observation(result, "rework", T12).status is AssessStatus.INELIGIBLE
    assert _observation(result, "rework", T23).status is AssessStatus.INELIGIBLE
    assert _observation(result, "verbosity", "checkpoint_1").status is AssessStatus.OK
    assert _observation(result, "problem_verbosity", "trajectory").status is (
        AssessStatus.INELIGIBLE
    )


def test_unreached_checkpoints_leave_problem_means_unavailable_unlike_a_one_shot_problem(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    partial = _scored(memory_context(tmp_path / "partial", case, reached=2, planned=True))
    assert _observation(partial, "rework", T23).status is AssessStatus.NOT_APPLICABLE
    for metric in ("problem_verbosity", "problem_architecture", "problem_rework"):
        observation = _observation(partial, metric, "trajectory")
        assert (observation.status, observation.value) == (AssessStatus.UNAVAILABLE, None)
        assert "not_applicable" in (observation.reason or "")

    one_shot = _scored(memory_context(tmp_path / "one", case, reached=1))
    assert not [item for item in one_shot.observations if item.metric in {"rework", "regression"}]
    for metric in ("problem_rework", "problem_regression"):
        observation = _observation(one_shot, metric, "trajectory")
        assert observation.status is AssessStatus.NOT_APPLICABLE
    assert _observation(one_shot, "problem_architecture", "trajectory").status is AssessStatus.OK


def test_architecture_is_unavailable_without_an_entry_file_and_other_components_still_score(
    tmp_path: Path,
) -> None:
    result = _scored(memory_context(tmp_path, golden_case("synthetic_shop"), config={}))
    architecture = _observation(result, "architecture", "checkpoint_1")
    assert architecture.status is AssessStatus.UNAVAILABLE
    assert "entry_file" in (architecture.reason or "")
    assert _observation(result, "verbosity", "checkpoint_1").status is AssessStatus.OK
    assert _observation(result, "rework", T12).status is AssessStatus.OK


@pytest.mark.parametrize("entry_file", ["renamed_missing.py", "../app.py", "shop"])
def test_an_entry_file_absent_from_the_snapshot_makes_architecture_ineligible_never_best(
    tmp_path: Path, entry_file: str
) -> None:
    case = golden_case("synthetic_shop")
    result = _scored(memory_context(tmp_path, case, config={"entry_file": entry_file}))
    for checkpoint in case["checkpoints"]:
        architecture = _observation(result, "architecture", checkpoint["checkpoint"])
        assert (architecture.status, architecture.value) == (AssessStatus.INELIGIBLE, None)
        assert (architecture.reason or "").startswith("entry_file_absent: ")
    assert _observation(result, "verbosity", "checkpoint_1").status is AssessStatus.OK
    assert _observation(result, "problem_architecture", "trajectory").value is None


def test_a_trajectory_with_nothing_measurable_is_statused_as_a_whole(tmp_path: Path) -> None:
    case = golden_case("synthetic_shop")
    result = _scored(memory_context(tmp_path / "unreached", case, reached=0, planned=True))
    assert result.status is AssessStatus.UNAVAILABLE
    js = {
        "name": "js",
        "checkpoints": [{"checkpoint": "checkpoint_1", "files": {"web/app.js": "let x = 1;\n"}}],
    }
    result = _scored(memory_context(tmp_path / "js", js))
    assert result.status is AssessStatus.INELIGIBLE
    assert (result.reason or "").startswith("production_language_unsupported")
