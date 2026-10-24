"""Iteration 4 consumers: gate-effort in the analysis, review, manipulation check and grading.

- the analysis compares gate-effort's autofix outcomes (each with its better direction);
- the markdown shows the autofix-by-family and fix-cost-by-refusal tables only when an arm
  reports them;
- the manipulation check, the review gate view and the pre-fix grading read gate-effort;
- forge's ordering case is a known, spec-determined timing sensitivity (not an erratum),
  shown beside the grading-vs-coverage disagreement count.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from raidar.design.problem.sensitivities import (
    SensitivityError,
    SensitivityKind,
    known_sensitivities,
    load_sensitivities,
)
from raidar.evaluation.analysis.assessed import GATE_EFFORT_REF, disagreements
from raidar.evaluation.analysis.gate_effort import GateDescriptive
from raidar.evaluation.analysis.markdown import (
    _autofix_family_table,
    _better,
    _refusal_table,
    _verifier_arms,
)
from raidar.evaluation.analysis.report import ArmSummary
from raidar.evaluation.review.quality.gate_view import GATE_EFFORT_REF as REVIEW_REF
from raidar.grading.offline import PRE_FIX_GRADE_SCORERS
from raidar.scoring.catalog.checks import GATE_EFFORT_REFS as MANIPULATION_REFS
from raidar.scoring.catalog.outcomes import GATE_METRICS, GATE_PREFIX, VERIFIER_DISAGREEMENTS
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation
from raidar.scoring.gate_effort.autofix_metrics import REFUSAL_KINDS
from raidar.scoring.gate_effort.families import FAMILIES

REPO = Path(__file__).resolve().parents[2]

HEADLINE_AUTOFIX = {
    "total_autofix_rounds",
    "total_autofix_errors",
    "total_autofix_files_changed",
    "total_autofix_lines_changed",
    "total_autofix_removed_blocking",
    "first_pass_clean_post_autofix_rate",
    "total_new_first_run_blocking_post_autofix_net",
    "final_step_end_blocking_post_autofix_net",
    "total_new_first_run_blocking_raw_net",
    "total_fix_sessions_with_coverage_minimum",
    "total_fix_cost_usd_with_coverage_minimum",
    "total_fix_wall_s_with_coverage_minimum",
}


def test_gate_effort_autofix_outcomes_are_compared_each_with_a_better_direction() -> None:
    compared = {metric.id.removeprefix(GATE_PREFIX) for metric in GATE_METRICS}

    assert compared >= HEADLINE_AUTOFIX
    assert {f"total_autofix_removed_blocking.{family}" for family in FAMILIES} <= compared
    assert {f"total_fix_cost_usd_by_refusal.{kind}" for kind in REFUSAL_KINDS} <= compared
    assert _better("gate.first_pass_clean_post_autofix_rate") == "↑ higher"
    assert _better("gate.total_new_first_run_blocking_post_autofix_net") == "↓ lower"
    assert _better("gate.total_fix_cost_usd_by_refusal.coverage_minimum") == "↓ lower"


def test_every_consumer_reads_gate_effort() -> None:
    assert MANIPULATION_REFS == (GATE_EFFORT_REF,)
    assert REVIEW_REF == GATE_EFFORT_REF
    assert GATE_EFFORT_REF in PRE_FIX_GRADE_SCORERS


def _arm(name: str, values: dict[str, float]) -> ArmSummary:
    return ArmSummary(
        arm=name,
        planned=1,
        run=1,
        valid=1,
        infra_error=0,
        not_run=0,
        infra_reasons={},
        cost_means={},
        gate_effort={
            metric: GateDescriptive(n=1, mean=value, sd=None, interval=None)
            for metric, value in values.items()
        },
    )


def test_the_autofix_and_refusal_tables_show_only_when_an_arm_reports_them() -> None:
    plain = _arm("r2", {"gate.total_fix_cost_usd": 0.1})
    assert _autofix_family_table([plain], ["r2"]) == []
    assert _refusal_table([plain], ["r2"]) == []
    reporting = _arm(
        "r2",
        {
            "gate.total_autofix_removed_blocking": 3.0,
            "gate.total_autofix_removed_blocking.style": 3.0,
            "gate.total_fix_cost_usd_by_refusal.mixed": 0.05,
            "gate.total_fix_wall_s_by_refusal.mixed": 120.0,
            "gate.total_fix_cost_usd_by_refusal.coverage_minimum": 0.02,
            "gate.total_fix_wall_s_by_refusal.coverage_minimum": 60.0,
        },
    )
    families = "\n".join(_autofix_family_table([reporting], ["r2"]))
    refusals = "\n".join(_refusal_table([reporting], ["r2"]))
    assert "| style | 3 |" in families.replace("3.000", "3").replace("3.0", "3")
    assert "coverage_minimum" in refusals
    assert "mixed" in refusals


# ---- item 5: a known verifier sensitivity, shown beside the disagreement count ---------------

FORGE = "scb/forge@38d627ecf668"
TIMING_CASE = "test_checkpoint_1.py::test_blueprint_list_orders_by_created_at_name_uuid"


def test_the_forge_ordering_case_is_a_spec_determined_timing_sensitivity_not_an_erratum() -> None:
    found = known_sensitivities([FORGE, "toy@1"], start=REPO)

    note = next(note for note in found[FORGE] if note.case == TIMING_CASE)
    assert (note.case, note.kind, note.spec_determined) == (
        TIMING_CASE,
        SensitivityKind.TIMING,
        True,
    )
    assert "instruction.md:64" in note.spec
    errata = (REPO / "lab/problems/registry/scb/forge/38d627ecf668.errata.yaml").read_text()
    assert "test_blueprint_list_orders_by_created_at_name_uuid" not in errata


def test_an_invalid_or_mismatched_sensitivities_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "x.sensitivities.yaml"
    assert load_sensitivities(path, problem=FORGE) == ()
    path.write_text("schema: raidar.problem-sensitivities/1\nproblem: other@1\nsensitivities: []\n")
    with pytest.raises(SensitivityError):
        load_sensitivities(path, problem=FORGE)
    good = (REPO / "lab/problems/registry/scb/forge/38d627ecf668.sensitivities.yaml").read_text()
    path.write_text(good)
    with pytest.raises(SensitivityError, match="names problem"):
        load_sensitivities(path, problem="scb/other@1")


def _cq3(*counts: float | None) -> AssessResult:
    observations = [
        Observation(
            metric="regression_parity_excluded_cases",
            subject=f"t{index}",
            status=AssessStatus.OK if count is not None else AssessStatus.NOT_APPLICABLE,
            value=count,
            reason=None if count is not None else "no run",
        )
        for index, count in enumerate(counts)
    ]
    measured = any(count is not None for count in counts)
    status = AssessStatus.OK if measured else AssessStatus.NOT_APPLICABLE
    return AssessResult(
        status=status, reason=None if measured else "none", observations=tuple(observations)
    )


def test_disagreements_sum_code_qualitys_excluded_cases() -> None:
    assert disagreements(_cq3(0, 1, None, 1)).value == 2
    assert disagreements(_cq3(None)).status == "not_applicable"
    assert not disagreements(None).measured


def test_the_verifier_section_shows_the_counts_and_the_known_notes() -> None:
    arm = _arm("r2", {})
    arm = arm.model_copy(
        update={
            "verifier_diagnostics": {
                VERIFIER_DISAGREEMENTS: GateDescriptive(n=4, mean=1.0, sd=0.0, interval=None)
            }
        }
    )
    notes = known_sensitivities([FORGE], start=REPO)
    report = SimpleNamespace(
        arms=(arm,),
        verifier_sensitivities={
            ref: [note.model_dump(mode="json") for note in found] for ref, found in notes.items()
        },
    )

    text = "\n".join(_verifier_arms(report))  # type: ignore[arg-type]

    assert "## Verifier sensitivity (diagnostic; never an outcome)" in text
    assert VERIFIER_DISAGREEMENTS in text
    assert TIMING_CASE in text
    assert _verifier_arms(SimpleNamespace(arms=(_arm("r1", {}),))) == []  # type: ignore[arg-type]
