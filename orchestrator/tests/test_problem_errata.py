"""Versioned problem errata: loading, lock pinning, verifier records and dual reporting."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from raidar.design.experiments.lock import LockError
from raidar.design.experiments.metrics import SCB_CANONICAL
from raidar.design.experiments.validation import semantic_issues
from raidar.design.problems.errata import (
    Disposition,
    ErrataError,
    errata_ref_from_metadata,
    load_errata,
)
from raidar.design.problems.models import ProblemId
from raidar.design.problems.store import ROOT_ENV
from raidar.engine.jobs.assessment import lock_errata, lock_rescore_config
from raidar.engine.jobs.plan import PlanError, PlanOptions, build_plan
from raidar.engine.trials.trial_config import harbor_trial_config
from raidar.evaluation.analysis.assessed import CORRECTNESS_REF, build_outcome
from raidar.grading.errata import ErrataOutcome, ErrataRef, errata_outcome
from raidar.grading.models import CaseStatus
from raidar.grading.records import load_step_record, step_record_dir, write_step_record
from raidar.scoring.correctness import correctness_scorer
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)
from raidar.scoring.framework.trial_inputs import load_trial_context
from raidar.scoring.framework.validation import validate_result
from raidar.subjects.environments.python.models import load_pack_spec
from tests.execution_support import ImportedFixture, locked_experiment, validated_fixture
from tests.scorer_support import APP_V1, StepFixture, write_trial
from tests.test_analysis_outcomes import _record
from tests.test_verifier_and_regrade import make_verifier, place_step_artifact
from tests.verifier_support import FakeAgentEnvironment

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


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ImportedFixture:
    built = validated_fixture(tmp_path / "problems")
    monkeypatch.setenv(ROOT_ENV, str(built.store.root))
    return built


# ---- the errata file ---------------------------------------------------------------


def test_errata_is_pinned_by_its_bytes_and_refused_for_another_manifest(
    fixture: ImportedFixture,
) -> None:
    path = write_errata(fixture)
    problem = ProblemId.parse(fixture.ref)
    manifest = fixture.store.load_manifest(problem)
    loaded = fixture.store.load_errata(problem, manifest)
    assert loaded is not None
    assert loaded.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded.lock_metadata() == {
        "sha256": loaded.sha256,
        "exclude_from_solved": [EXCLUDED],
        "informational": ["test_checkpoint_1.py::test_adds"],
    }
    assert loaded.ref() == ErrataRef(sha256=loaded.sha256, exclude_from_solved=(EXCLUDED,))
    assert loaded.errata.ids(Disposition.INFORMATIONAL) == ("test_checkpoint_1.py::test_adds",)

    with pytest.raises(ErrataError, match="for manifest"):
        load_errata(path, problem=fixture.ref, manifest_sha256="0" * 64)
    with pytest.raises(ErrataError, match="errata of"):
        load_errata(path, problem="other/p@abcdefabcdef", manifest_sha256=manifest.digest())
    write_errata(fixture, cases=[errata_document("x", "y")["cases"][0]] * 2)
    with pytest.raises(ErrataError, match="duplicate errata case ids"):
        load_errata(path, problem=fixture.ref, manifest_sha256=manifest.digest())
    write_errata(
        fixture,
        cases=[
            {
                **errata_document("x", "y")["cases"][0],
                "reason": "TODO: state why the specification does not determine the asserted value",
            }
        ],
    )
    with pytest.raises(ErrataError, match="placeholder reason"):
        load_errata(path, problem=fixture.ref, manifest_sha256=manifest.digest())
    path.unlink()
    assert fixture.store.load_errata(problem, manifest) is None
    assert errata_ref_from_metadata({"checkpoints": 2}) is None


def test_the_lock_pins_the_errata_in_the_execution_identity(
    fixture: ImportedFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The trial config attaches host codex auth; this test is about errata, not the host.
    monkeypatch.setattr(
        "raidar.engine.trials.trial_config.with_host_auth", lambda config, harness: config
    )
    _, plain, _ = locked_experiment(
        tmp_path / "plain", fixture.ref, environment={"compose_init": False}
    )
    write_errata(fixture)
    loaded, pinned, _ = locked_experiment(
        tmp_path / "pinned", fixture.ref, environment={"compose_init": False}
    )
    metadata = pinned["execution"]["references"]["problem"][fixture.ref]["metadata"]
    assert metadata["errata"]["exclude_from_solved"] == [EXCLUDED]
    assert pinned["lock_sha256"] != plain["lock_sha256"]
    assert lock_errata(pinned) == {
        fixture.ref: ErrataRef(sha256=metadata["errata"]["sha256"], exclude_from_solved=(EXCLUDED,))
    }
    assert lock_errata(plain) == {}

    plan = build_plan(
        loaded, pinned, store=fixture.store, pack=load_pack_spec(), options=PlanOptions()
    )
    assert plan.problems[0].errata == lock_errata(pinned)[fixture.ref].model_dump(mode="json")
    trial = plan.trials[0]
    config = harbor_trial_config(
        plan,
        trial,
        tmp_path / "task",
        trials_dir=tmp_path / "trials",
        bundle_root=tmp_path / "bundle",
        docker_context="orbstack",
        errata=plan.problems[0].errata,
    )
    assert config.verifier.kwargs["errata"] == {
        "sha256": metadata["errata"]["sha256"],
        "exclude_from_solved": [EXCLUDED],
    }

    # Changing the committed errata after locking refuses the plan: relock first.
    write_errata(fixture, registered="2026-10-03")
    with pytest.raises(PlanError, match="errata changed since the lock"):
        build_plan(
            loaded, pinned, store=fixture.store, pack=load_pack_spec(), options=PlanOptions()
        )
    write_errata(fixture, manifest_sha256="0" * 64)
    with pytest.raises(LockError, match="for manifest"):
        locked_experiment(tmp_path / "stale", fixture.ref, environment={"compose_init": False})


def test_errata_reach_correctness_config_from_the_lock_only(
    fixture: ImportedFixture, tmp_path: Path
) -> None:
    write_errata(fixture)
    loaded, lock, _ = locked_experiment(
        tmp_path / "exp", fixture.ref, environment={"compose_init": False}
    )
    config = lock_rescore_config(lock).scorers["correctness@1"]
    assert config["errata"] == {
        fixture.ref: {
            "sha256": lock_errata(lock)[fixture.ref].sha256,
            "exclude_from_solved": [EXCLUDED],
        }
    }
    assert config["progression"] == "continue"
    explicit = loaded.spec.model_copy(
        update={
            "assessment": loaded.spec.assessment.model_copy(
                update={"scorer_config": {"correctness@1": {"errata": {}}}}
            )
        }
    )
    issues = [issue.render() for issue in semantic_issues(explicit)]
    assert any("assessment.scorer_config.correctness@1.errata" in issue for issue in issues)


# ---- the verifier record -------------------------------------------------------------


async def test_the_verifier_records_raw_and_errata_applied_verdicts(tmp_path: Path) -> None:
    place_step_artifact(tmp_path, "checkpoint_2", mul=False)
    errata = {"sha256": "e" * 64, "exclude_from_solved": [EXCLUDED]}
    verifier = await make_verifier(
        tmp_path, FakeAgentEnvironment(), step="checkpoint_2", errata=errata
    )
    result = await verifier.verify()
    record = load_step_record(step_record_dir(tmp_path / "trial", "checkpoint_2"))
    # The reward and the grade stay the raw SCB verdict ...
    assert result.rewards is not None
    assert result.rewards["solved"] == 0.0
    assert not record.grade.solved
    # ... and the errata-applied one is recorded beside it.
    assert record.errata == ErrataOutcome(
        sha256="e" * 64,
        excluded_cases=(EXCLUDED,),
        excluded_failing=(EXCLUDED,),
        solved=True,
    )


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
