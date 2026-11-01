"""Consumers of verification-effort and verifier diagnostics: the analysis markdown.

- the analysis compares every verification-effort outcome with a better direction;
- the gate section states what blocking_checks counts;
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
from raidar.evaluation.analysis.assessed import disagreements
from raidar.evaluation.analysis.markdown import _better, _gate_arms, _verifier_arms
from raidar.evaluation.analysis.verification_effort import GateDescriptive
from raidar.scoring.catalog.outcomes import GATE_METRICS, VERIFIER_DISAGREEMENTS
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation
from tests.analysis_support import arm_summary

REPO = Path(__file__).resolve().parents[2]


def test_every_verification_effort_outcome_is_compared_with_a_better_direction() -> None:
    assert all(_better(metric.id) != "—" for metric in GATE_METRICS)
    assert _better("gate.first_pass_clean_rate") == "↑ higher"
    assert _better("gate.total_blocking_checks") == "↓ lower"
    assert _better("gate.total_fix_cost_usd") == "↓ lower"


def test_the_gate_section_says_what_blocking_checks_counts() -> None:
    arm = arm_summary("r1", {"gate.total_blocking_checks": 3.0})
    report = SimpleNamespace(arms=(arm,), gate_scorer=None)

    text = "\n".join(_gate_arms(report))  # type: ignore[arg-type]

    assert "| gate.total_blocking_checks | ↓ lower |" in text
    assert "failed blocking gate checks" in text
    ungated = SimpleNamespace(
        arms=(arm.model_copy(update={"verification_effort": None}),), gate_scorer=None
    )
    assert _gate_arms(ungated) == []  # type: ignore[arg-type]


# ---- item 5: a known verifier sensitivity, shown beside the disagreement count ---------------

FORGE = "scb/forge@38d627ecf668"
TIMING_CASE = "test_checkpoint_1.py::test_blueprint_list_orders_by_created_at_name_uuid"


def test_the_forge_ordering_case_is_a_spec_determined_timing_sensitivity_not_an_erratum() -> None:
    found = known_sensitivities([FORGE, "toy@1"], store=REPO / "lab" / "problems")

    note = next(note for note in found[FORGE] if note.case == TIMING_CASE)
    assert (note.case, note.kind, note.spec_determined) == (
        TIMING_CASE,
        SensitivityKind.TIMING,
        True,
    )
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
    arm = arm_summary("r2", {})
    arm = arm.model_copy(
        update={
            "verifier_diagnostics": {
                VERIFIER_DISAGREEMENTS: GateDescriptive(n=4, mean=1.0, sd=0.0, interval=None)
            }
        }
    )
    notes = known_sensitivities([FORGE], store=REPO / "lab" / "problems")
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
    assert _verifier_arms(SimpleNamespace(arms=(arm_summary("r1", {}),))) == []  # type: ignore[arg-type]
