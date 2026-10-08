"""Iteration 4 consumers: gate-effort@9 in the analysis, review, manipulation check and grading.

- the analysis compares gate-effort@9's new outcomes only for @9 (each with its better
  direction), and @8 reads none of them;
- the markdown shows the autofix-by-family and fix-cost-by-refusal tables only when an arm
  reports them;
- the manipulation check, the review gate view and the pre-fix grading read @9 (before @8);
- forge's ordering case is a known, spec-determined timing sensitivity (not an erratum),
  shown beside the grading-vs-coverage disagreement count.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from raidar.analysis.assessed import disagreements
from raidar.analysis.gate_effort import GateDescriptive
from raidar.analysis.manipulation import GATE_EFFORT_REFS as MANIPULATION_REFS
from raidar.analysis.markdown import (
    _autofix_family_table,
    _better,
    _refusal_table,
    _verifier_arms,
)
from raidar.analysis.outcomes import GATE_PREFIX, VERIFIER_DISAGREEMENTS, gate_metrics_for
from raidar.analysis.report import ArmSummary
from raidar.commands.assess_grading import PRE_FIX_GRADE_SCORERS
from raidar.problems.sensitivities import (
    SensitivityError,
    SensitivityKind,
    known_sensitivities,
    load_sensitivities,
)
from raidar.review_quality.gate_view import GATE_EFFORT_REFS as REVIEW_REFS
from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.gate_effort_v9.manifest import REFUSAL_KINDS
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Observation

REPO = Path(__file__).resolve().parents[2]

HEADLINE_V9 = {
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


def test_gate_effort_v9_outcomes_are_compared_only_for_v9() -> None:
    v9 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@9")}
    v8 = {metric.id.removeprefix(GATE_PREFIX) for metric in gate_metrics_for("gate-effort@8")}

    added = v9 - v8
    assert added >= HEADLINE_V9
    assert {f"total_autofix_removed_blocking.{family}" for family in FAMILIES} <= added
    assert {f"total_fix_cost_usd_by_refusal.{kind}" for kind in REFUSAL_KINDS} <= added
    assert not added & v8
    assert _better("gate.first_pass_clean_post_autofix_rate") == "↑ higher"
    assert _better("gate.total_new_first_run_blocking_post_autofix_net") == "↓ lower"
    assert _better("gate.total_fix_cost_usd_by_refusal.coverage_minimum") == "↓ lower"


def test_every_consumer_reads_v9_before_v8() -> None:
    for refs in (MANIPULATION_REFS, REVIEW_REFS):
        assert refs.index("gate-effort@9") < refs.index("gate-effort@8")
    assert "gate-effort@9" in PRE_FIX_GRADE_SCORERS


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


def test_the_autofix_and_refusal_tables_show_only_for_v9() -> None:
    v8 = _arm("r2", {"gate.total_fix_cost_usd": 0.1})
    assert _autofix_family_table([v8], ["r2"]) == []
    assert _refusal_table([v8], ["r2"]) == []
    v9 = _arm(
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
    families = "\n".join(_autofix_family_table([v9], ["r2"]))
    refusals = "\n".join(_refusal_table([v9], ["r2"]))
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
    errata = (REPO / "problems/registry/scb/forge/38d627ecf668.errata.yaml").read_text()
    assert "test_blueprint_list_orders_by_created_at_name_uuid" not in errata


def test_an_invalid_or_mismatched_sensitivities_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "x.sensitivities.yaml"
    assert load_sensitivities(path, problem=FORGE) == ()
    path.write_text("schema: raidar.problem-sensitivities/1\nproblem: other@1\nsensitivities: []\n")
    with pytest.raises(SensitivityError):
        load_sensitivities(path, problem=FORGE)
    good = (REPO / "problems/registry/scb/forge/38d627ecf668.sensitivities.yaml").read_text()
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


def test_disagreements_sum_code_quality_v3s_excluded_cases() -> None:
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
