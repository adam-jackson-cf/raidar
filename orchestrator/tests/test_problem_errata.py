"""Versioned problem errata: loading, lock pinning, verifier records and dual reporting."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from raidar.design.problem.models import ProblemId
from raidar.design.problem.store import ROOT_ENV
from raidar.evaluation.analysis.assessed import CORRECTNESS_REF, build_outcome
from raidar.grading.errata import ErrataOutcome, ErrataRef, errata_outcome
from raidar.grading.models import CaseStatus
from raidar.grading.records import load_step_record, step_record_dir, write_step_record
from raidar.scoring.correctness import correctness_scorer
from raidar.scoring.framework.metrics import SCB_CANONICAL
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)
from raidar.scoring.framework.trial_inputs import load_trial_context
from raidar.scoring.framework.validation import validate_result
from tests.execution_support import ImportedFixture, validated_fixture
from tests.scorer_support import APP_V1, StepFixture, write_trial
from tests.test_analysis_outcomes import _record

P, F = CaseStatus.PASSED, CaseStatus.FAILED
EXCLUDED = "test_checkpoint_2.py::test_multiplies"


def errata_document(problem: str, manifest_sha256: str, **changes: Any) -> dict[str, Any]:
    case = {
        "id": EXCLUDED,
        "checkpoint_origin": "checkpoint_2",
        "disposition": "exclude-from-solved",
        "rule": "wording-unstated",
        "assertion": "assert result.stdout.strip() == '6'",
        "reason": "the specification does not state the output wording",
        "evidence": ["audit: wording-unstated"],
    }
    note = {**case, "id": "test_checkpoint_1.py::test_adds", "disposition": "informational"}
    document = {
        "kind": "raidar-problem-errata",
        "schema_version": 1,
        "problem": problem,
        "manifest_sha256": manifest_sha256,
        "registered": "2026-10-02",
        "basis": "spec-determinacy audit v1",
        "cases": [case, note],
    }
    return {**document, **changes}


def write_errata(fixture: ImportedFixture, **changes: Any) -> Path:
    problem = ProblemId.parse(fixture.ref)
    manifest = fixture.store.load_manifest(problem)
    path = fixture.store.errata_path(problem)
    document = {**errata_document(fixture.ref, manifest.digest()), **changes}
    path.write_text(yaml.safe_dump(document))
    return path


@pytest.fixture(name="fixture")
def fixture_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ImportedFixture:
    built = validated_fixture(tmp_path / "problems")
    monkeypatch.setenv(ROOT_ENV, str(built.store.root))
    return built


# ---- the errata file ---------------------------------------------------------------


# ---- the verifier record -------------------------------------------------------------


def test_errata_outcome_has_no_verdict_without_a_raw_one() -> None:
    from raidar.grading.models import InfraFailure, VerifierGrade

    grade = VerifierGrade(
        step="s", checkpoint="s", cases=(), infra_failure=InfraFailure(code="sandbox_timeout")
    )
    assert errata_outcome(grade, ErrataRef(sha256="x")).solved is None


# ---- correctness dual reporting ---------------------------------------------------------


def _trial(tmp_path: Path, statuses: list[tuple[CaseStatus, ...]], planned=()) -> Path:
    return write_trial(
        tmp_path / "trial",
        [
            StepFixture(f"checkpoint_{index + 1}", APP_V1, step)
            for index, step in enumerate(statuses)
        ],
        planned=planned,
    )


def _observed(trial: Path, config: dict[str, Any]) -> dict[tuple[str, str], Observation]:
    ctx = load_trial_context(trial, config=config)
    scorer = correctness_scorer()
    result = validate_result(scorer.manifest, ctx, scorer.assess(ctx))
    return {(item.metric, item.subject): item for item in result.observations}


def _errata(*cases: str, sha256: str = "a" * 64) -> dict[str, Any]:
    return {"toy": {"sha256": sha256, "exclude_from_solved": list(cases)}}


def test_correctness_reports_raw_and_errata_applied_solves_side_by_side(tmp_path: Path) -> None:
    # checkpoint_2's only failing case is excluded by the errata; checkpoint_3 fails for real.
    trial = _trial(tmp_path, [(P, P), (P, F), (F, P)])
    observed = _observed(trial, {"errata": _errata("test_checkpoint_2.py::case_1")})
    raw = [observed["solved", f"checkpoint_{k}"].value for k in (1, 2, 3)]
    errata = [observed["solved_errata", f"checkpoint_{k}"].value for k in (1, 2, 3)]
    assert raw == [1.0, 0.0, 0.0]
    assert errata == [1.0, 1.0, 0.0]
    assert observed["solved_checkpoints", TRAJECTORY_SUBJECT].value == 1
    assert observed["solved_checkpoints_errata", TRAJECTORY_SUBJECT].value == 2
    assert observed["reached", TRAJECTORY_SUBJECT].value == 1
    assert observed["reached_errata", TRAJECTORY_SUBJECT].value == 2
    # Pass rates never change under an errata.
    assert observed["strict_pass_rate", "checkpoint_2"].value == 0.5


def test_errata_metrics_are_statused_without_an_errata_or_on_a_digest_mismatch(
    tmp_path: Path,
) -> None:
    trial = _trial(tmp_path, [(P,), (F,)])
    none = _observed(trial, {})
    for key in (("solved_errata", "checkpoint_1"), ("reached_errata", TRAJECTORY_SUBJECT)):
        assert none[key].status is AssessStatus.NOT_APPLICABLE
        assert "no errata is pinned for toy" in (none[key].reason or "")
    assert none["solved", "checkpoint_1"].value == 1.0

    record_dir = step_record_dir(trial, "checkpoint_1")
    record = load_step_record(record_dir)
    recorded = ErrataOutcome(sha256="b" * 64, excluded_cases=(), excluded_failing=(), solved=True)
    write_step_record(record_dir, record.model_copy(update={"errata": recorded}))
    mismatch = _observed(trial, {"errata": _errata()})
    assert mismatch["solved_errata", "checkpoint_2"].status is AssessStatus.INELIGIBLE
    assert "verifier recorded errata bbbbbbbbbbbb" in (
        mismatch["solved_checkpoints_errata", TRAJECTORY_SUBJECT].reason or ""
    )


def test_halted_trials_keep_the_raw_stop_under_errata(tmp_path: Path) -> None:
    trial = _trial(tmp_path, [(P,), (P, F)], planned=["checkpoint_3"])
    config = {"progression": "halt_on_failure", "errata": _errata("test_checkpoint_2.py::case_1")}
    observed = _observed(trial, config)
    assert observed["solved_checkpoints", TRAJECTORY_SUBJECT].value == 1
    # The halted checkpoint is solved under errata; the unattempted one stays unsolved.
    assert observed["solved_checkpoints_errata", TRAJECTORY_SUBJECT].value == 2
    assert observed["reached_errata", TRAJECTORY_SUBJECT].status is AssessStatus.UNAVAILABLE
    assert observed["solved_errata", "checkpoint_3"].status is AssessStatus.NOT_APPLICABLE


def test_analysis_labels_errata_outcomes_beside_the_raw_ones() -> None:
    def ok(metric: str, subject: str, value: float) -> Observation:
        return Observation(metric=metric, subject=subject, status=AssessStatus.OK, value=value)

    correctness = AssessResult(
        status=AssessStatus.OK,
        observations=tuple(
            ok(metric, step, value)
            for step, raw, fixed in (
                ("checkpoint_1", 1.0, 1.0),
                ("checkpoint_2", 0.0, 1.0),
                ("checkpoint_3", 0.0, 1.0),
            )
            for metric, value in (
                ("solved", raw),
                ("solved_errata", fixed),
                ("strict_pass_rate", 0.9),
            )
        )
        + (
            ok("solved_checkpoints", TRAJECTORY_SUBJECT, 1.0),
            ok("reached", TRAJECTORY_SUBJECT, 1.0),
            ok("solved_checkpoints_errata", TRAJECTORY_SUBJECT, 3.0),
            ok("reached_errata", TRAJECTORY_SUBJECT, 3.0),
        ),
    )
    outcome = build_outcome(_record(), {CORRECTNESS_REF: correctness}, policy=SCB_CANONICAL)
    assert outcome.metrics["solve_rate"].value == pytest.approx(1 / 3)
    assert outcome.metrics["solve_rate_errata"].value == 1.0
    assert outcome.metrics["reached_errata"].value == 3.0
    assert outcome.metrics["final_solved"].value == 0.0
    assert outcome.metrics["final_solved_errata"].value == 1.0


def test_the_analysis_report_names_the_errata_its_errata_outcomes_apply() -> None:
    from raidar.evaluation.analysis.markdown import _errata_lines
    from raidar.evaluation.analysis.report import AnalysisReport

    pinned = {
        "scb/forge@x": {"sha256": "9a6dec369dea" + "0" * 52, "exclude_from_solved": ["a", "b"]}
    }
    (line,) = _errata_lines(
        AnalysisReport.model_construct(errata=pinned, primary_metric="solve_rate")
    )
    assert "`scb/forge@x` errata `9a6dec369dea` (2 exclude-from-solved cases)" in line
    assert "every other outcome, including the primary, is the raw SCB grade" in line
    (errata_primary,) = _errata_lines(
        AnalysisReport.model_construct(errata=pinned, primary_metric="solve_rate_errata")
    )
    assert "including the primary" not in errata_primary
    assert "the primary (`solve_rate_errata`) is one of them" in errata_primary
    assert _errata_lines(AnalysisReport.model_construct(errata=None)) == []
