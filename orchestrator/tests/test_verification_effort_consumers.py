"""Consumers of verification-effort and verifier diagnostics: the analysis markdown.

- the gate section lists the gated arms and is empty for an ungated trajectory;
- the verifier section shows the grading-vs-coverage disagreement count and known notes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from raidar.design.problem.sensitivities import (
    SensitivityError,
    known_sensitivities,
    load_sensitivities,
)
from raidar.evaluation.analysis.assessed import disagreements
from raidar.evaluation.analysis.markdown import _gate_arms, _verifier_arms
from raidar.evaluation.analysis.verification_effort import GateDescriptive
from raidar.scoring.catalog.outcomes import VERIFIER_DISAGREEMENTS
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation
from tests.analysis_support import arm_summary

REPO = Path(__file__).resolve().parents[2]


def test_the_gate_section_lists_a_gated_arm_and_is_empty_without_a_gate() -> None:
    arm = arm_summary("r1", {"gate.total_blocking_checks": 3.0})
    report = SimpleNamespace(arms=(arm,), gate_scorer=None)

    text = "\n".join(_gate_arms(report))  # type: ignore[arg-type]
    assert "| gate.total_blocking_checks | ↓ lower |" in text

    ungated = SimpleNamespace(
        arms=(arm.model_copy(update={"verification_effort": None}),), gate_scorer=None
    )
    assert _gate_arms(ungated) == []  # type: ignore[arg-type]


# ---- item 5: a known verifier sensitivity, shown beside the disagreement count ---------------

FORGE = "scb/forge@38d627ecf668"
TIMING_CASE = "test_checkpoint_1.py::test_blueprint_list_orders_by_created_at_name_uuid"


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
