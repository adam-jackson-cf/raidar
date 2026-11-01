"""Quality projection decisions on in-memory inputs: invariants, source matching, precedence.

The synthetic design is built without a run directory or store (``synthetic_quality``); the
real-directory seams are proved in ``test_review_projection_integrity``.
"""

from __future__ import annotations

import hashlib
import json
import runpy
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from raidar.contract.job_layout import JOB_KIND, JOB_MANIFEST, PLAN_FILE
from raidar.contract.trial import CheckpointState
from raidar.design.experiment.lock import default_lock_path
from raidar.evaluation.analysis.assessed import build_outcome
from raidar.evaluation.analysis.sources import JobDir
from raidar.evaluation.review.projection.assessments import TrialAssessments
from raidar.evaluation.review.quality.experiment_view import ReviewedTrial, _primary
from raidar.evaluation.review.quality.gate_view import GateReader, build_gate_view
from raidar.evaluation.review.quality.labels import JevAudit
from raidar.evaluation.review.quality.models import COMPONENTS, QualityProjection
from raidar.evaluation.review.quality.projection import (
    _portable,
    assemble_projection,
    build_quality_projection,
    build_quality_projection_from,
    main,
    review_trials,
)
from raidar.evaluation.review.quality.sources import (
    StoreIndex,
    TrialSource,
    _experiment_info,
    _job_dirs,
    _matches,
    discover_trials,
    job_trials,
)
from raidar.evaluation.review.quality.stats import pearson, quantiles
from raidar.scoring.catalog.outcomes import (
    CODE_QUALITY_REF,
    CORRECTNESS_METRIC,
    CORRECTNESS_REF,
    HEADLINE_SCORE,
    VERIFICATION_EFFORT_REF,
)
from raidar.scoring.framework.metrics import SCB_CANONICAL
from raidar.scoring.framework.rescore_config import RescoreConfig
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.framework.store import Assessment, assessment_key
from raidar.scoring.judge.audit import AuditLog, LabelRecord
from raidar.scoring.judge.rubric import RUBRIC_POLICY
from raidar.scoring.verification_effort.records import GateRecordError, StepGate
from tests.review_fixtures.quality.generate import (
    CHECKPOINTS,
    EXPERIMENT,
    INELIGIBLE_H,
    INVALID,
    PROBLEM,
    RUN_ID,
    SCORERS,
    SyntheticQuality,
    synthetic_quality,
)
from tests.review_fixtures.quality.lock import write_synthetic_lock

RUN_PREFIX = f"{EXPERIMENT}.{RUN_ID}."
LATER = "2026-10-01T00:00:00.000Z"
SECOND_RUN = "synthetic-run-2"


@pytest.fixture(scope="module")
def full() -> SyntheticQuality:
    return synthetic_quality()


@pytest.fixture(scope="module")
def data(full: SyntheticQuality) -> SyntheticQuality:
    """The first two attempts (both arms): enough for decisions about trials and runs."""
    return replace(full, trials=tuple(s for s in full.trials if s.record.trial.attempt < 2))


def _project(
    data: SyntheticQuality,
    *,
    trials: Sequence[TrialSource] | None = None,
    find: Callable[[str, TrialSource], Assessment | None] | None = None,
    audit: JevAudit | None = None,
    reader: GateReader | None = None,
    integrity: Mapping[Path, str] | None = None,
) -> QualityProjection:
    reviewed = review_trials(
        data.trials if trials is None else trials,
        find or data.find,
        audit or data.audit(),
        reader or data.read_gate,
    )
    return assemble_projection(
        reviewed, generated_from="memory", synthetic=True, integrity=integrity or {}
    )


@pytest.fixture(scope="module")
def projection(full: SyntheticQuality) -> QualityProjection:
    return _project(full)


def _with_experiment(data: SyntheticQuality, **changes: object) -> tuple[TrialSource, ...]:
    return tuple(
        replace(source, experiment=replace(source.experiment, **changes)) for source in data.trials
    )


def _sha(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _clone_run(data: SyntheticQuality, job_id: str, **changes: object) -> SyntheticQuality:
    """A second run of the same trials (same snapshots), finishing later."""
    experiment = replace(data.trials[0].experiment, **changes)
    clones = tuple(
        replace(
            source,
            job_id=job_id,
            job_dir=source.job_dir.with_name(job_id),
            finished_at=LATER,
            experiment=experiment,
        )
        for source in data.trials
    )
    assessments = dict(data.assessments)
    gates = dict(data.gates)
    for source, clone in zip(data.trials, clones, strict=True):
        for ref in SCORERS:
            original = data.assessments[(source.review_id, ref)]
            rekeyed = original.model_copy(update={"key": _sha(original.key, job_id)})
            assessments[(clone.review_id, ref)] = rekeyed
        for checkpoint in CHECKPOINTS:
            agent = Path("steps") / checkpoint / "agent"
            if source.trial_dir / agent in data.gates:
                gates[clone.trial_dir / agent] = data.gates[source.trial_dir / agent]
    return replace(data, trials=(*data.trials, *clones), assessments=assessments, gates=gates)


def _source(data: SyntheticQuality, trial: str) -> TrialSource:
    return next(item for item in data.trials if item.record.trial.trial_id == trial)


def _problem(projection: QualityProjection):
    (experiment,) = projection.experiments
    (problem,) = experiment.problems
    return problem


class _Lookup:
    """A store that has each trial's Assessments by trial directory name."""

    def __init__(self, data: SyntheticQuality | None) -> None:
        self.found: dict[str, dict[str, Assessment]] = {}
        if data is None:
            return
        for source in data.trials:
            self.found[source.trial_dir.name] = {
                ref: data.assessments[(source.review_id, ref)] for ref in SCORERS
            }

    def for_trial(
        self,
        trial_dir: Path,
        scorer_refs: Sequence[str],
        settings: RescoreConfig,
        *,
        config_path: Path | None = None,
    ) -> TrialAssessments:
        found = self.found.get(trial_dir.name, {})
        return TrialAssessments(found={ref: found[ref] for ref in scorer_refs if ref in found})


def _locked(data: SyntheticQuality) -> tuple[TrialSource, ...]:
    """The trials with the lock's assessment settings, so the store index will look them up."""
    return _with_experiment(data, assessment=RescoreConfig())


def _unlocked(data: SyntheticQuality) -> tuple[TrialSource, ...]:
    """The trials of a run whose own lock cannot be found."""
    info = _experiment_info({"lock_sha256": _sha("lock")})
    return tuple(replace(source, experiment=info) for source in data.trials)


# ---- the Quality view of one trial and its experiment ----------------------------------


def test_every_cell_is_statused_and_never_a_silent_zero(projection: QualityProjection) -> None:
    assert projection.synthetic
    for detail in projection.run_details.values():
        cells = [cell for row in detail.rows for cell in row.cells.values()]
        cells += list(detail.problem.values())
        for cell in cells:
            assert (cell.value is not None) == (cell.status == "ok")
            assert cell.status == "ok" or cell.reason
        assert set(detail.problem) == set(COMPONENTS)


def test_first_checkpoint_has_no_transition_and_later_ones_use_the_one_into_them(
    projection: QualityProjection,
) -> None:
    detail = projection.run_details[f"{RUN_PREFIX}b00-p0-baseline"]
    first, second = detail.rows[0], detail.rows[1]
    assert first.cells["R"].status == "not_applicable"
    assert "first checkpoint" in (first.cells["R"].reason or "")
    assert first.cells["I"].status == "not_applicable"
    assert second.cells["R"].status == "ok"
    weights = SCB_CANONICAL.inertia_weights
    expected = sum(
        (weights[key] * Decimal(str(second.cells[key].value)) for key in weights), Decimal(0)
    )
    assert second.cells["I"].value == pytest.approx(float(expected))
    c_value, i_value = second.cells["C"].value, second.cells["I"].value
    assert c_value is not None
    assert i_value is not None
    assert second.cells["S"].value == pytest.approx(100 * c_value * (0.7 + 0.3 * i_value))


def test_problem_row_is_the_analysis_outcome_of_the_same_assessments(
    full: SyntheticQuality, projection: QualityProjection
) -> None:
    """C = mean per-checkpoint case pass rate (ADR-001 §2.4, the fork's C_p) and S agree
    with WS-J for every trial."""
    for detail in projection.run_details.values():
        source = next(item for item in full.trials if item.review_id == detail.run.id)
        results = {ref: full.assessments[(detail.run.id, ref)].result for ref in detail.run.sources}
        outcome = build_outcome(source.record, results, policy=SCB_CANONICAL)
        rates = {
            item.subject: item.value or 0.0
            for item in results[CORRECTNESS_REF].observations
            if item.metric == "strict_pass_rate" and item.status is AssessStatus.OK
        }
        steps = source.record.trial.steps
        if len(rates) == len(steps):
            expected = sum(rates[step] for step in steps) / len(steps)
            assert detail.problem["C"].value == pytest.approx(expected)
        assert detail.problem["C"].value == outcome.metrics[CORRECTNESS_METRIC].value
        assert detail.problem["S"].status == outcome.metrics[HEADLINE_SCORE].status
        assert detail.problem["S"].value == outcome.metrics[HEADLINE_SCORE].value


def test_problem_row_is_the_headline_policy_score(projection: QualityProjection) -> None:
    detail = projection.run_details[f"{RUN_PREFIX}b01-p0-guided"]
    assert (detail.policy.ref, detail.policy.source) == ("scb-canonical@1", "lock")
    components = {key: detail.problem[key].value for key in ("C", "V", "E", "H", "R", "G")}
    scored = SCB_CANONICAL.score(
        checkpoints=len(detail.rows),
        components={key: Decimal(str(value)) for key, value in components.items()},
    )
    assert detail.problem["S"].value == pytest.approx(float(scored.score or 0))
    assert detail.run.score == detail.problem["S"]


def test_the_locks_headline_policy_takes_precedence_over_the_default(
    data: SyntheticQuality,
) -> None:
    result = _project(data, trials=_with_experiment(data, metric="scb-canonical-oneshot@1"))
    detail = result.run_details[f"{RUN_PREFIX}b00-p0-baseline"]
    assert (detail.policy.ref, detail.policy.source) == ("scb-canonical-oneshot@1", "lock")
    assert detail.problem["R"].status == "not_applicable"
    assert detail.problem["S"].status == "ineligible"
    assert any("checkpoint_count_unsupported" in reason for reason in detail.policy_reasons)


def test_an_ineligible_component_makes_the_headline_ineligible_with_reasons(
    projection: QualityProjection,
) -> None:
    arm, attempt, checkpoint = INELIGIBLE_H
    detail = projection.run_details[f"{RUN_PREFIX}b{attempt:02d}-p0-{arm}"]
    row = next(row for row in detail.rows if row.checkpoint == checkpoint)
    assert row.cells["H"].status == "ineligible"
    assert row.cells["I"].status == "ineligible"
    assert detail.problem["S"].status == "ineligible"
    assert any("component_ineligible H" in reason for reason in detail.policy_reasons)


def test_drilldown_evidence_points_at_the_checkpoint_snapshot(
    projection: QualityProjection,
) -> None:
    detail = projection.run_details[f"{RUN_PREFIX}b00-p0-baseline"]
    components = {item.component for item in detail.evidence}
    assert components >= {"V", "E", "R"}
    for item in detail.evidence:
        assert item.snapshot_path is not None
        assert item.snapshot_path.startswith(f"{EXPERIMENT}/{RUN_ID}/trials/")
        assert f"steps/{item.checkpoint}/artifacts/app/{item.path}" in item.snapshot_path
    high = [item for item in detail.evidence if item.kind == "high_complexity"]
    counts = {row.checkpoint: row.signals.high_cc_count for row in detail.rows}
    assert sum(value or 0 for value in counts.values()) == len(high)


def test_each_run_carries_its_trial_record_as_the_join_key_to_the_run_review(
    projection: QualityProjection,
) -> None:
    for run in projection.runs:
        assert run.trial_record == f"jobs/{EXPERIMENT}/{RUN_ID}/trial-records/{run.trial}.json"


def test_arm_statistics_leave_out_invalid_trials_and_pair_by_attempt(
    projection: QualityProjection,
) -> None:
    problem = _problem(projection)
    arm, attempt = INVALID
    assert problem.arms == ("baseline", "guided")
    assert any("infra-excluded" in item for item in problem.excluded)
    erosion = next(item for item in problem.paired if item.component == "E")
    assert erosion.n == 5
    assert f"attempt {attempt}: {arm} infra error" in erosion.excluded
    # Five pairs cannot reach p < 0.05 under the exact sign-flip test (smallest p is 0.0625).
    assert erosion.verdict == "inconclusive"
    assert "exact sign-flip" in erosion.reason
    headline = next(item for item in problem.paired if item.component == "S")
    assert headline.n == 4
    assert headline.verdict == "insufficient"
    assert headline.reason == "4 pairs < minimum 5 (pinned by the lock)"


def test_curves_and_distributions_cover_every_arm_and_checkpoint(
    projection: QualityProjection,
) -> None:
    problem = _problem(projection)
    for arm in problem.arms:
        assert set(problem.curves[arm]) == set(COMPONENTS)
        points = problem.curves[arm]["E"]
        assert [point.checkpoint for point in points] == list(problem.checkpoints)
        assert all(point.low is not None and point.low <= (point.mean or 0) for point in points)
    assert {item.signal for item in problem.distributions} == {
        "lint_per_kloc",
        "high_cc_mass_share",
        "high_cc_function_lines",
    }


def test_head_to_head_uses_per_checkpoint_medians_of_both_arms(
    projection: QualityProjection,
) -> None:
    problem = _problem(projection)
    h2h = problem.head_to_head
    assert h2h is not None
    assert (h2h.arm_a, h2h.arm_b) == ("baseline", "guided")
    lint = next(signal for signal in h2h.signals if signal.signal == "lint_findings")
    assert [point.checkpoint for point in lint.points] == list(problem.checkpoints)
    first = lint.points[0]
    attempts = [
        detail
        for detail in projection.run_details.values()
        if detail.run.arm == "baseline" and detail.run.valid
    ]
    values = sorted(float(detail.rows[0].signals.lint_findings or 0) for detail in attempts)
    assert first.a == quantiles(values)[2]  # type: ignore[index]
    assert first.n_a == len(values)


def test_sensitivity_ablation_matches_the_score_formula(projection: QualityProjection) -> None:
    problem = _problem(projection)
    sensitivity = problem.sensitivity
    eligible = [
        detail.problem
        for detail in projection.run_details.values()
        if detail.run.valid and detail.problem["S"].value is not None
    ]
    assert sensitivity.n == len(eligible)
    expected = sum(
        30 * (cells["C"].value or 0) * 0.3 * (cells["R"].value or 0) for cells in eligible
    )
    assert sensitivity.ablation["R"] == pytest.approx(expected)
    assert len(sensitivity.correlations) == 10
    assert sensitivity.excluded


def test_experiments_label_the_primary_outcome_as_the_analysis_report_does(
    data: SyntheticQuality, projection: QualityProjection
) -> None:
    (experiment,) = [item for item in projection.experiments if item.experiment == EXPERIMENT]
    assert (experiment.primary_outcome, experiment.primary_source, experiment.primary_raw) == (
        "solve_rate_solvable",
        "default for continue progression",
        "solve_rate",
    )
    first = data.trials[0]
    analysis = {**(first.experiment.analysis or {}), "primary_outcome": "solve_rate_errata"}
    configured = replace(first, experiment=replace(first.experiment, analysis=analysis))
    member = ReviewedTrial(source=configured, detail=projection.run_details[first.review_id])
    assert _primary([member]) == (
        "solve_rate_errata",
        "lock (analysis.primary_outcome)",
        "solve_rate",
    )


def test_trials_of_runs_under_different_locks_are_never_pooled(data: SyntheticQuality) -> None:
    cloned = _clone_run(replace(data, trials=data.trials[:2]), SECOND_RUN, lock_sha256="0" * 64)
    result = _project(cloned)
    by_lock = {item.lock_sha256: item for item in result.experiments}
    assert len(by_lock) == 2
    assert by_lock["0" * 64].jobs == (SECOND_RUN,)
    original = next(item for lock, item in by_lock.items() if lock != "0" * 64)
    assert original.jobs == (RUN_ID,)
    assert not any("superseded" in item for item in original.problems[0].excluded)
    assert original.problems[0].baseline_arm == "baseline"


def test_a_later_run_of_the_same_trials_supersedes_the_earlier_one(data: SyntheticQuality) -> None:
    result = _project(_clone_run(replace(data, trials=data.trials[:2]), SECOND_RUN))
    (experiment,) = result.experiments
    assert experiment.jobs == (RUN_ID, SECOND_RUN)
    problem = _problem(result)
    assert any("superseded" in item for item in problem.excluded)


# ---- trials the arm statistics leave out ---------------------------------------------


def test_truncated_trials_have_no_problem_outcome_and_stay_out_of_arm_statistics(
    data: SyntheticQuality,
) -> None:
    """A max_checkpoints run never scores its unrun checkpoints as zero (F6)."""
    trials = _unlocked(data)
    record = _source(data, "b00-p0-guided").record
    truncated = record.model_copy(
        update={
            "truncated_to": 1,
            "trial": record.trial.model_copy(update={"steps": ("checkpoint_1",)}),
            "step_results": (
                record.step_results[0],
                *(
                    step.model_copy(
                        update={"snapshot": None, "checkpoint_state": CheckpointState.NOT_ATTEMPTED}
                    )
                    for step in record.step_results[1:]
                ),
            ),
        }
    )
    trials = tuple(
        replace(source, record=truncated)
        if source.record.trial.trial_id == "b00-p0-guided"
        else source
        for source in trials
    )
    # The default policy is chosen by the problem's K (every planned step_result), not by the one
    # step that ran: no one-shot default for a 5-checkpoint problem.
    result = _project(data, trials=trials, find=StoreIndex(_Lookup(data)).find)
    detail = result.run_details[f"{RUN_PREFIX}b00-p0-guided"]
    assert detail.run.truncated_to == 1
    assert (detail.policy.ref, detail.policy.source) == ("scb-canonical@1", "default_by_k")
    assert {cell.status for cell in detail.problem.values()} == {"truncated"}
    assert all("max_checkpoints" in (cell.reason or "") for cell in detail.problem.values())
    assert any("development job" in note for note in detail.notes)
    assert [row.checkpoint for row in detail.rows] == ["checkpoint_1"]
    problem = _problem(result)
    assert any("b00-p0-guided: development job" in item for item in problem.excluded)
    # Without a matching lock there is no authorized assessment configuration.
    assert problem.curves["guided"]["E"][0].n == 0


def test_agent_override_trials_render_but_stay_out_of_arm_statistics(
    data: SyntheticQuality,
) -> None:
    overridden = replace(
        _source(data, "b00-p0-guided"),
        record=_source(data, "b00-p0-guided").record.model_copy(
            update={"agent_override": "oracle"}
        ),
    )
    trials = tuple(
        overridden if source.review_id == overridden.review_id else source for source in data.trials
    )
    result = _project(data, trials=trials)
    detail = result.run_details[f"{RUN_PREFIX}b00-p0-guided"]
    assert "agent override: oracle" in detail.notes
    problem = _problem(result)
    assert any("agent override oracle" in item for item in problem.excluded)
    erosion = next(item for item in problem.paired if item.component == "E")
    assert "attempt 0: guided not run" in erosion.excluded


def test_one_arm_has_no_head_to_head_and_small_samples_have_no_correlation(
    data: SyntheticQuality,
) -> None:
    trials = tuple(
        source
        for source in data.trials
        if source.record.trial.arm == "baseline" and source.record.trial.attempt < 2
    )
    problem = _problem(_project(data, trials=trials))
    assert problem.arms == ("baseline",)
    assert problem.head_to_head is None
    assert problem.paired == ()
    assert all(
        item.r is None and item.reason == "n=2 < 3" for item in problem.sensitivity.correlations
    )


def test_a_baseline_with_no_counted_trials_is_named_in_the_notes(data: SyntheticQuality) -> None:
    trials = tuple(
        replace(source, record=source.record.model_copy(update={"agent_override": "oracle"}))
        if source.record.trial.arm == "baseline"
        else source
        for source in data.trials
    )
    problem = _problem(_project(data, trials=trials))
    assert problem.baseline_arm == "baseline"
    assert "baseline" not in problem.arms
    assert problem.paired == ()
    assert any("baseline arm baseline has no counted trials" in note for note in problem.notes)


@pytest.mark.parametrize("quality_gap", ["missing", "ineligible"])
def test_zero_correctness_remains_scored_when_sensitivity_components_are_unmeasured(
    data: SyntheticQuality, quality_gap: str
) -> None:
    def adjusted(assessment: Assessment) -> Assessment:
        ref = assessment.scorer.ref
        observations = []
        for observation in assessment.result.observations:
            if ref == CORRECTNESS_REF and observation.metric == "strict_pass_rate":
                observation = observation.model_copy(update={"value": 0.0})
            elif ref == CODE_QUALITY_REF and observation.metric == "problem_architecture":
                observation = observation.model_copy(
                    update={
                        "status": AssessStatus.INELIGIBLE,
                        "value": None,
                        "reason": "unmeasured architecture",
                    }
                )
            observations.append(observation)
        result = assessment.result.model_copy(update={"observations": tuple(observations)})
        return assessment.model_copy(update={"result": result})

    assessments = {
        key: adjusted(assessment)
        for key, assessment in data.assessments.items()
        if not (quality_gap == "missing" and key[1] == CODE_QUALITY_REF)
    }
    changed = replace(data, assessments=assessments)
    result = _project(changed, trials=_with_experiment(data, metric="scb-canonical@2"))
    detail = result.run_details[f"{RUN_PREFIX}b00-p0-baseline"]
    assert (detail.problem["S"].status, detail.problem["S"].value) == ("ok", 0)
    sensitivity = _problem(result).sensitivity
    assert sensitivity.n == 0
    assert sensitivity.ablation == {}
    assert all(item.r is None for item in sensitivity.correlations)
    assert any("sensitivity components unavailable" in item for item in sensitivity.excluded)


# ---- verification effort (verification-effort + the gate/1 records) ---------------------


def test_a_gated_run_shows_its_gate_rounds_and_failed_checks(
    projection: QualityProjection,
) -> None:
    gated = projection.run_details[f"{RUN_PREFIX}b00-p0-guided"].gate
    assert gated is not None
    assert gated.source is not None
    assert gated.scorer == VERIFICATION_EFFORT_REF
    assert [step.checkpoint for step in gated.steps] == [f"checkpoint_{n}" for n in range(1, 6)]
    unresolved = gated.steps[3]
    assert unresolved.cells["unresolved"].value == 1.0
    assert unresolved.cells["rounds_to_clean"].status == "not_applicable"
    assert [item.verdict for item in unresolved.rounds] == ["refused"] * 4
    assert [item.run for item in unresolved.rounds] == [1, 2, 3, 4]
    first = unresolved.rounds[0]
    assert first.after == "stage_1_work"
    assert first.attribution == "refused"
    assert first.failed_checks
    # The step cells are the stored Assessment's: failed blocking checks sum over the runs.
    assert unresolved.cells["blocking_checks"].value == sum(
        len(item.failed_checks) for item in unresolved.rounds
    )
    assert gated.totals["unresolved_rate"].value == pytest.approx(0.2)
    clean = gated.steps[0]
    assert [item.verdict for item in clean.rounds] == ["passed"]
    assert clean.rounds[0].failed_checks == ()
    # A trial without a gate/1 record has no gate view at all (never zeros).
    assert projection.run_details[f"{RUN_PREFIX}b00-p0-baseline"].gate is None


def test_gate_records_without_an_assessment_or_with_a_broken_record_say_so(
    data: SyntheticQuality,
) -> None:
    broken = _source(data, "b01-p0-guided").trial_dir / "steps" / "checkpoint_2" / "agent"

    def read(trace_dir: Path) -> StepGate | None:
        if trace_dir == broken:
            raise GateRecordError("workflow.json is not JSON")
        return data.read_gate(trace_dir)

    def find(ref: str, trial: TrialSource) -> Assessment | None:
        return None if ref == VERIFICATION_EFFORT_REF else data.find(ref, trial)

    rebuilt = _project(data, find=find, reader=read)

    gate = rebuilt.run_details[f"{RUN_PREFIX}b01-p0-guided"].gate
    assert gate is not None
    assert gate.source is None
    assert gate.steps[0].cells["gate_runs"].status == "unassessed"
    assert gate.steps[1].rounds == ()
    assert any(note.startswith("checkpoint_2: gate record unreadable") for note in gate.notes)
    assert gate.steps[3].rounds[0].verdict == "refused"


def test_a_step_directory_outside_the_trial_is_never_read(data: SyntheticQuality) -> None:
    source = _source(data, "b00-p0-guided")
    record = source.record.model_copy(
        update={
            "step_results": (
                source.record.step_results[0].model_copy(update={"trace_dir": "../../escape"}),
                *source.record.step_results[1:],
            )
        }
    )
    read: list[Path] = []

    def reader(trace_dir: Path) -> StepGate | None:
        read.append(trace_dir)
        return data.read_gate(trace_dir)

    gate = build_gate_view(
        replace(source, record=record),
        data.find(VERIFICATION_EFFORT_REF, source),
        CHECKPOINTS,
        reader,
    )
    assert gate is not None
    assert gate.steps[0].rounds == ()
    assert any(note.startswith("checkpoint_1: step directory is outside") for note in gate.notes)
    assert len(read) == len(CHECKPOINTS) - 1
    assert all(source.trial_dir in path.parents for path in read)


def test_a_trial_with_neither_assessment_nor_record_on_disk_has_no_gate_view(
    data: SyntheticQuality,
) -> None:
    # The default reader finds no agent directory under a trial that was never written.
    assert build_gate_view(_source(data, "b00-p0-guided"), None, CHECKPOINTS) is None


# ---- Jev decisions ---------------------------------------------------------------------


def test_jev_decisions_attach_to_their_trial(projection: QualityProjection) -> None:
    labelled = projection.run_details[f"{RUN_PREFIX}b00-p0-baseline"].jev_decisions
    assert len(labelled) == 3
    assert {item.policy for item in labelled} == {RUBRIC_POLICY}
    assert projection.run_details[f"{RUN_PREFIX}b03-p0-baseline"].jev_decisions == ()


def test_jev_decisions_never_attach_to_a_run_they_may_not_belong_to(
    data: SyntheticQuality,
) -> None:
    """A trial id shared by several runs attaches nowhere; a run-qualified name attaches once."""
    cloned = _clone_run(data, SECOND_RUN)
    qualified = f"{SECOND_RUN}/b01-p0-baseline"
    renamed = tuple(
        decision.model_copy(update={"trial": qualified})
        if decision.trial == "b01-p0-baseline"
        else decision
        for decision in cloned.decisions
    )
    result = _project(replace(cloned, decisions=renamed))
    for job_id in (RUN_ID, SECOND_RUN):
        detail = result.run_details[f"{EXPERIMENT}.{job_id}.b00-p0-baseline"]
        assert detail.jev_decisions == ()
        assert any("not attached" in note and "2 runs share" in note for note in detail.notes)
    assert len(result.run_details[f"{EXPERIMENT}.{SECOND_RUN}.b01-p0-baseline"].jev_decisions) == 3
    assert result.run_details[f"{RUN_PREFIX}b01-p0-baseline"].jev_decisions == ()


def test_the_jev_audit_reads_decisions_and_their_current_labels_from_a_store(
    data: SyntheticQuality, tmp_path: Path
) -> None:
    audit = AuditLog.for_store(tmp_path)
    for decision in data.decisions:
        audit.append(decision)
    audit.append(data.decisions[0].model_copy(update={"trial": None, "request_id": "unbound"}))
    labelled = data.decisions[0]
    audit.append_label(
        LabelRecord(
            timestamp="2026-10-02T00:00:00.000Z",
            request_id=labelled.request_id,
            policy=labelled.policy,
            label="correct",
        )
    )
    read = JevAudit.open(tmp_path)
    assert sorted(item.request_id for items in read.decisions.values() for item in items) == sorted(
        decision.request_id for decision in data.decisions
    )
    assert {request: record.label for request, record in read.labels.items()} == {
        labelled.request_id: "correct"
    }
    reviewed = review_trials(data.trials[:1], data.find, read, data.read_gate)
    items = reviewed[0].detail.jev_decisions
    assert sorted(item.label or "" for item in items) == ["", "", "correct"]
    assert JevAudit.open(tmp_path / "no-store") == JevAudit(decisions={}, labels={})


# ---- which stored Assessment a trial gets -----------------------------------------------


def test_assessments_are_matched_by_snapshot_content_not_trial_name(
    data: SyntheticQuality,
) -> None:
    """The store holds an Assessment under this trial's name, but of other snapshots."""
    first = data.trials[0]
    record = first.record.model_copy(
        update={
            "step_results": tuple(
                step.model_copy(
                    update={"snapshot": step.snapshot.model_copy(update={"digest": "f" * 64})}
                )
                for step in first.record.step_results
                if step.snapshot is not None
            )
        }
    )
    other = replace(first, job_id=SECOND_RUN, job_dir=first.job_dir.with_name(SECOND_RUN))
    other = replace(other, record=record)
    trials = (*_locked(data), *_locked(replace(data, trials=(other,))))
    result = _project(data, trials=trials, find=StoreIndex(_Lookup(data)).find)
    unmatched = result.run_details[other.review_id]
    assert unmatched.run.sources == {CODE_QUALITY_REF: None, CORRECTNESS_REF: None}
    assert unmatched.rows[0].cells["V"].status == "unavailable"
    matched = result.run_details[first.review_id]
    assert matched.run.sources[CODE_QUALITY_REF] is not None
    assert any("superseded" in item for item in _problem(result).excluded)


class _KeyedStore:
    """Assessments by key; a trial is looked up under the key its locked settings derive."""

    def __init__(self, locked: Assessment, others: Sequence[Assessment]) -> None:
        self.locked = locked
        self.by_key = {item.key: item for item in (locked, *others)}

    def for_trial(
        self,
        trial_dir: Path,
        scorer_refs: Sequence[str],
        settings: RescoreConfig,
        *,
        config_path: Path | None = None,
    ) -> TrialAssessments:
        key = assessment_key(
            self.locked.scorer,
            config=self.locked.config,
            tool_lock=self.locked.tool_lock,
            inputs=self.locked.inputs,
        )
        found = self.by_key.get(key)
        return TrialAssessments(found={CORRECTNESS_REF: found} if found else {})


@pytest.mark.parametrize("changed", ["config", "implementation", "tools", "verifier", "trace"])
def test_only_the_locked_exact_assessment_is_used_even_when_a_newer_one_has_the_same_snapshots(
    data: SyntheticQuality, changed: str
) -> None:
    source = _locked(data)[0]
    original = data.find(CORRECTNESS_REF, source)
    assert original is not None
    original = original.model_copy(
        update={
            "key": assessment_key(
                original.scorer,
                config=original.config,
                tool_lock=original.tool_lock,
                inputs=original.inputs,
            )
        }
    )
    updates: dict[str, object] = {}
    if changed == "config":
        updates["config"] = {**original.config, "new_setting": True}
    elif changed == "implementation":
        updates["scorer"] = original.scorer.model_copy(update={"implementation_digest": "e" * 64})
    elif changed == "tools":
        updates["tool_lock"] = {"another": "tool"}
    else:
        inputs = json.loads(json.dumps(original.inputs))
        inputs["steps"][0]["oracle_record" if changed == "verifier" else "trace"] = "e" * 64
        updates["inputs"] = inputs
    newer = original.model_copy(update=updates)
    newer = newer.model_copy(
        update={
            "key": assessment_key(
                newer.scorer,
                config=newer.config,
                tool_lock=newer.tool_lock,
                inputs=newer.inputs,
            ),
            "result": original.result.model_copy(update={"observations": ()}),
        }
    )
    assert newer.key != original.key
    assert StoreIndex(_KeyedStore(original, [newer])).find(CORRECTNESS_REF, source) == original
    assert StoreIndex(_KeyedStore(original, [])).find(CORRECTNESS_REF, source) == original
    gone = _KeyedStore(original, [newer])
    del gone.by_key[original.key]
    assert StoreIndex(gone).find(CORRECTNESS_REF, source) is None


def test_the_store_index_serves_only_the_locks_scorers_for_a_locked_trial(
    data: SyntheticQuality,
) -> None:
    source = _locked(data)[0]
    index = StoreIndex(_Lookup(data))
    assert index.find(CORRECTNESS_REF, source) == data.find(CORRECTNESS_REF, source)
    assert index.find(CODE_QUALITY_REF, source) == data.find(CODE_QUALITY_REF, source)
    assert index.find(VERIFICATION_EFFORT_REF, source) == data.find(VERIFICATION_EFFORT_REF, source)
    narrowed = replace(source, experiment=replace(source.experiment, scorers=(CORRECTNESS_REF,)))
    assert StoreIndex(_Lookup(data)).find(CODE_QUALITY_REF, narrowed) is None
    unlocked = replace(source, experiment=replace(source.experiment, assessment=None))
    assert StoreIndex(_Lookup(data)).find(CORRECTNESS_REF, unlocked) is None
    assert StoreIndex(_Lookup(None)).find(CORRECTNESS_REF, source) is None


@pytest.mark.parametrize(
    ("stored", "digests", "matches"),
    [
        ({"a": "x", "b": "y"}, {"a": "x", "b": "y"}, True),
        ({"a": "x", "b": None}, {"a": "x", "b": "y"}, False),
        ({"a": "x"}, {"a": "x", "b": "y"}, False),
        ({"a": "z", "b": "y"}, {"a": "x", "b": "y"}, False),
        ({"a": "x", "b": "y", "c": "w"}, {"a": "x", "b": "y"}, False),
        ({"a": "x", "b": "y", "c": None}, {"a": "x", "b": "y"}, True),
        ({"a": "x", "b": None}, {"a": "x", "b": None}, True),
        ({"a": "x"}, {"a": None}, False),
        ({}, {}, False),
    ],
)
def test_a_stored_assessment_matches_only_the_same_snapshots_at_every_step(
    stored: dict[str, str | None], digests: dict[str, str | None], matches: bool
) -> None:
    assert _matches(stored, digests) is matches


# ---- the run's lock --------------------------------------------------------------------


def _lock_beside(
    directory: Path, *, experiment: str = EXPERIMENT, metric: str = "scb-canonical@1"
) -> tuple[Path, str]:
    """A sealed lock beside ``directory/experiment.yaml`` and the lock digest it carries."""
    experiment_file = directory / "experiment.yaml"
    digest = write_synthetic_lock(
        experiment_file,
        experiment=experiment,
        baseline_arm="baseline",
        metric=metric,
        problem=PROBLEM,
        checkpoints=len(CHECKPOINTS),
    )
    return default_lock_path(experiment_file), digest


def test_the_lock_the_run_executed_under_supplies_the_baseline_metric_and_scorers(
    tmp_path: Path,
) -> None:
    lock, digest = _lock_beside(tmp_path, metric="scb-canonical-oneshot@1")
    run = {"lock_sha256": digest, "experiment_file": str(tmp_path / "experiment.yaml")}
    info = _experiment_info(run)
    assert info.note is None
    assert (info.lock_sha256, info.baseline_arm, info.metric) == (
        digest,
        "baseline",
        "scb-canonical-oneshot@1",
    )
    assert set(info.scorers) == set(SCORERS)
    assert info.lock_path == lock
    assert info.assessment is not None
    assert (info.analysis or {})["baseline_arm"] == "baseline"


def test_a_lock_given_by_flag_is_tried_before_the_one_beside_the_experiment_file(
    tmp_path: Path,
) -> None:
    """A relocked experiment file (another experiment) must not hide the executed lock."""
    executed_dir = tmp_path / "executed"
    executed_dir.mkdir()
    flagged, digest = _lock_beside(executed_dir)
    other_dir = tmp_path / "relocked"
    other_dir.mkdir()
    default, relocked = _lock_beside(other_dir, experiment=f"{EXPERIMENT}-redesigned")
    assert relocked != digest
    run = {"lock_sha256": digest, "experiment_file": str(other_dir / "experiment.yaml")}
    assert _experiment_info(run, (flagged,)).lock_path == flagged
    # Without the flag, the relocked lock is all there is.
    assert _experiment_info(run).note is not None


@pytest.mark.parametrize("change", ["missing", "relocked", "unreadable", "none", "unrecorded"])
def test_without_the_runs_own_lock_there_is_no_baseline_and_no_paired_delta(
    data: SyntheticQuality, tmp_path: Path, change: str
) -> None:
    """The baseline comes from the lock the run executed under, never from another experiment's."""
    _, digest = _lock_beside(tmp_path)
    experiment_file = tmp_path / "experiment.yaml"
    run: dict[str, object] = {"lock_sha256": digest, "experiment_file": str(experiment_file)}
    if change == "missing":
        default_lock_path(experiment_file).unlink()
    elif change == "relocked":
        _lock_beside(tmp_path, experiment=f"{EXPERIMENT}-redesigned")
    elif change == "unreadable":
        default_lock_path(experiment_file).write_text("[", encoding="utf-8")
    elif change == "none":
        run = {"lock_sha256": digest}
    else:
        run = {}
    info = _experiment_info(run)
    if change in {"missing", "relocked", "unreadable"}:
        info = _experiment_info(run)
    assert info.baseline_arm is None
    assert info.assessment is None
    assert info.note
    assert str(tmp_path) not in info.note
    if change == "relocked":
        assert "but the run was executed under" in info.note
        assert "experiment file unavailable" not in info.note
    trials = tuple(replace(source, experiment=info) for source in data.trials)
    result = _project(data, trials=trials, find=StoreIndex(_Lookup(data)).find)
    problem = _problem(result)
    assert problem.baseline_arm is None
    assert problem.paired == ()
    assert any("no baseline arm" in note for note in problem.notes)
    detail = result.run_details[f"{RUN_PREFIX}b00-p0-baseline"]
    assert info.note in detail.notes
    assert all(key is None for key in detail.run.sources.values())
    assert (detail.policy.ref, detail.policy.source) == ("scb-canonical@1", "default_by_k")


# ---- sources, discovery decisions and assembly ----------------------------------------


def test_trial_sources_name_their_record_and_job_portably(data: SyntheticQuality) -> None:
    source = data.trials[0]
    assert source.review_id == f"{EXPERIMENT}.{RUN_ID}.b00-p0-baseline"
    assert source.truncation is None
    assert source.portable_path(source.trial_dir / "steps") == (
        f"{EXPERIMENT}/{RUN_ID}/trials/b00-p0-baseline/steps"
    )
    truncated = replace(source, record=source.record.model_copy(update={"truncated_to": 2}))
    assert truncated.truncation is not None
    assert "max_checkpoints" in truncated.truncation
    absolute = source.record.model_copy(update={"trial_dir": "/elsewhere/trial"})
    assert replace(source, record=absolute).trial_dir == Path("/elsewhere/trial")
    unsafe = source.record.model_copy(update={"experiment": "a b/c"})
    assert replace(source, record=unsafe).review_id == f"a-b-c.{RUN_ID}.b00-p0-baseline"


def test_a_runs_trials_come_from_its_manifest_and_records(data: SyntheticQuality) -> None:
    records = tuple(source.record for source in data.trials[:2])
    manifest = {"job_id": "named-job", "finished_at": LATER, "lock_sha256": _sha("lock")}
    job = JobDir(path=Path("jobs/x/dir-name"), manifest=manifest, plan={}, records=records)
    named = job_trials(Path("jobs"), job)
    assert [(item.job_id, item.finished_at, item.record) for item in named] == [
        ("named-job", LATER, record) for record in records
    ]
    assert all(item.job is job and item.experiment.lock_sha256 == _sha("lock") for item in named)
    bare = job_trials(Path("jobs"), JobDir(Path("jobs/x/dir-name"), {}, {}, records))
    assert {(item.job_id, item.finished_at) for item in bare} == {("dir-name", "")}
    assert all(item.experiment.note for item in bare)


def test_only_raidar_job_manifests_are_discovered_as_runs(
    tmp_path: Path, data: SyntheticQuality
) -> None:
    for name, text in (
        ("run", json.dumps({"kind": JOB_KIND, "job_id": "found"})),
        ("planned", json.dumps({"kind": JOB_KIND, "job_id": "planned"})),
        ("other", json.dumps({"kind": "something-else"})),
        ("broken", "{not json"),
        ("list", "[]"),
    ):
        (tmp_path / name).mkdir()
        (tmp_path / name / JOB_MANIFEST).write_text(text, encoding="utf-8")
    (tmp_path / "planned" / PLAN_FILE).write_text("{}", encoding="utf-8")
    assert list(_job_dirs(tmp_path)) == [tmp_path / "planned", tmp_path / "run"]
    assert discover_trials(tmp_path) == []
    assert StoreIndex.open(tmp_path).find(CORRECTNESS_REF, _locked(data)[0]) is None


def test_a_component_missing_only_in_the_compared_arm_is_an_ineligible_pair(
    data: SyntheticQuality,
) -> None:
    target = _source(data, "b00-p0-guided").review_id
    assessments = dict(data.assessments)
    quality = assessments[(target, CODE_QUALITY_REF)]
    observations = tuple(
        item.model_copy(update={"status": AssessStatus.INELIGIBLE, "value": None, "reason": "x"})
        if item.metric == "problem_erosion"
        else item
        for item in quality.result.observations
    )
    assessments[(target, CODE_QUALITY_REF)] = quality.model_copy(
        update={"result": quality.result.model_copy(update={"observations": observations})}
    )
    erosion = next(
        item
        for item in _problem(_project(replace(data, assessments=assessments))).paired
        if item.component == "E"
    )
    assert erosion.n == 1
    assert "attempt 0: ineligible" in erosion.excluded


def test_evidence_of_metrics_without_a_component_is_not_drilled_down(
    data: SyntheticQuality,
) -> None:
    source = data.trials[0]
    quality = data.assessments[(source.review_id, CODE_QUALITY_REF)]
    extra = quality.result.evidence[0].model_copy(update={"metric": "lint_findings"})
    result = quality.result.model_copy(update={"evidence": (*quality.result.evidence, extra)})
    assessments = {
        **data.assessments,
        (source.review_id, CODE_QUALITY_REF): quality.model_copy(update={"result": result}),
    }
    plain = _project(data).run_details[source.review_id].evidence
    assert (
        _project(replace(data, assessments=assessments)).run_details[source.review_id].evidence
        == plain
    )


def test_review_ids_must_stay_unique_across_the_assembled_runs(data: SyntheticQuality) -> None:
    reviewed = review_trials(data.trials, data.find, data.audit(), data.read_gate)
    with pytest.raises(ValueError, match="share review ids"):
        assemble_projection(
            [*reviewed, reviewed[0]], generated_from="memory", synthetic=False, integrity={}
        )


def test_an_integrity_note_is_appended_to_every_run_of_its_job_only(
    data: SyntheticQuality,
) -> None:
    cloned = _clone_run(replace(data, trials=data.trials[:2]), SECOND_RUN)
    plain = _project(cloned)
    result = _project(cloned, integrity={data.trials[0].job_dir.resolve(): "provenance differs"})
    for trial_id, detail in result.run_details.items():
        before = plain.run_details[trial_id].notes
        if detail.run.job == RUN_ID:
            assert detail.notes == (*before, "provenance differs")
        else:
            assert detail.notes == before


def test_a_projection_needs_at_least_one_jobs_root_and_store() -> None:
    with pytest.raises(ValueError, match="at least one"):
        build_quality_projection_from([])


def test_portable_paths_are_relative_to_the_working_directory_or_just_a_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inside = tmp_path / "work"
    (inside / "runs" / "a").mkdir(parents=True)
    monkeypatch.chdir(inside)
    assert _portable(inside / "runs" / "a") == "runs/a"
    assert _portable(tmp_path / "outside") == "outside"


def test_a_projection_round_trips_through_the_cli_and_names_its_inputs_portably(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs, store = tmp_path / "jobs", tmp_path / "store"
    runs.mkdir()
    store.mkdir()
    output = tmp_path / "out" / "quality.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "raidar-quality-projection",
            "--jobs-root",
            str(runs),
            "--store",
            str(store),
            "--output",
            str(output),
            "--synthetic",
        ],
    )
    runpy.run_module("raidar.evaluation.review.quality", run_name="__main__")
    loaded = QualityProjection.model_validate_json(output.read_bytes())
    assert loaded.synthetic
    assert loaded.runs == ()
    assert loaded.experiments == ()
    assert str(tmp_path) not in output.read_text()
    assert build_quality_projection(runs, store).runs == ()


def test_cli_refuses_unpaired_runs_roots_and_stores(tmp_path: Path) -> None:
    runs = str(tmp_path)
    with pytest.raises(SystemExit):
        main(["--jobs-root", runs, "--jobs-root", runs, "--store", runs, "--output", "x.json"])


def test_descriptive_statistics_boundaries() -> None:
    assert quantiles([]) is None
    assert quantiles([3.0]) == (3.0, 3.0, 3.0, 3.0, 3.0)
    assert quantiles([1.0, 2.0, 3.0, 4.0, 5.0]) == (1.0, 2.0, 3.0, 4.0, 5.0)
    assert pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)


@pytest.mark.parametrize("duplicate", [False, True])
def test_projection_contract_refuses_ambiguous_or_incomplete_run_indexes(
    projection: QualityProjection, duplicate: bool
) -> None:
    payload = projection.model_dump(mode="json")
    if duplicate:
        payload["runs"].append(payload["runs"][0])
    else:
        del payload["run_details"][payload["runs"][0]["id"]]
    with pytest.raises(ValueError, match="duplicate ids|run index and run_details differ"):
        QualityProjection.model_validate(payload)


def test_older_trials_discovered_later_do_not_replace_newer_results(
    data: SyntheticQuality, projection: QualityProjection
) -> None:
    from raidar.evaluation.review.quality.experiment_view import _counted

    source = data.trials[0]
    old = ReviewedTrial(source=source, detail=projection.run_details[source.review_id])
    new_run = old.detail.run.model_copy(update={"id": "newer"})
    new = ReviewedTrial(
        source=replace(source, finished_at=LATER),
        detail=old.detail.model_copy(update={"run": new_run}),
    )
    counted, excluded, infra = _counted([new, old])
    assert counted == [new]
    assert len(excluded) == 1
    assert excluded[0].startswith(old.detail.run.id + ":")
    assert not infra


@pytest.mark.parametrize("kind", ["binary", None])
def test_quality_pairs_refuse_binary_or_undeclared_component_tests(
    kind: str | None,
) -> None:
    from raidar.evaluation.analysis.effects import ArmExclusions, Exclusions, Settings
    from raidar.evaluation.review.quality.experiment_view import _delta

    declarations = {"problems": {PROBLEM: {"binary": ["verbosity"]}}} if kind is not None else None
    exclusions = Exclusions(
        planned=6,
        paired=6,
        arms=tuple(ArmExclusions(arm=arm, planned=6, excluded=0) for arm in ("baseline", "gated")),
    )
    result = _delta(
        PROBLEM,
        "V",
        "gated",
        ([0.5] * 6, exclusions, []),
        Settings(
            min_pairs=2,
            bootstrap_samples=100,
            outcome_types=declarations,
            max_exclusion_difference=0.0,
        ),
    )
    assert result.verdict == "insufficient"
    assert ("binary" if kind else "declar") in result.reason


def test_constant_component_has_no_correlation() -> None:
    from raidar.evaluation.review.quality.experiment_view import _correlation

    result = _correlation("V", "E", [{"V": 0.5, "E": value} for value in (0.0, 0.5, 1.0)])
    assert result.r is None
    assert result.reason == "a component is constant"


def test_incompatible_primary_outcome_is_not_presented_as_a_measured_primary(
    data: SyntheticQuality, projection: QualityProjection
) -> None:
    source = data.trials[0]
    source = replace(
        source, experiment=replace(source.experiment, analysis={"primary_outcome": "bogus"})
    )
    assert _primary(
        [ReviewedTrial(source=source, detail=projection.run_details[source.review_id])]
    ) == (
        None,
        None,
        None,
    )


def test_mixed_headline_policies_are_named_in_problem_notes() -> None:
    from raidar.evaluation.review.quality.experiment_view import _problem_notes

    notes = _problem_notes(
        ["scb-canonical@1", "alternate@1"], "baseline", ["baseline", "gated"], None
    )
    assert len(notes) == 1
    assert "scb-canonical@1" in notes[0]
    assert "alternate@1" in notes[0]
