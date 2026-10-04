"""Quality projection (plan K2): contract invariants, source matching and precedence."""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from raidar.analysis.assessed import build_outcome
from raidar.analysis.outcomes import CORRECTNESS_METRIC, HEADLINE_SCORE
from raidar.execution.models import TrialRecord
from raidar.review_quality.models import COMPONENTS, QualityProjection
from raidar.review_quality.projection import build_quality_projection, main
from raidar.review_quality.stats import pearson, quantiles
from raidar.review_quality.synthetic import (
    EXPERIMENT,
    INELIGIBLE_H,
    INVALID,
    RUN_ID,
    generate_quality_fixture,
)
from raidar.review_quality.synthetic_lock import write_synthetic_lock
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Observation, RunStats
from raidar.scorers.store import OBJECTS_DIR, AssessmentStore
from raidar.spec.metrics import SCB_CANONICAL

RUN_PREFIX = f"{EXPERIMENT}.{RUN_ID}."


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return generate_quality_fixture(tmp_path_factory.mktemp("quality") / "fx")


@pytest.fixture(scope="module")
def projection(fixture_dir: Path) -> QualityProjection:
    return build_quality_projection(fixture_dir / "jobs", fixture_dir / "store", synthetic=True)


def _copy(fixture_dir: Path, tmp_path: Path) -> Path:
    target = tmp_path / "fx"
    shutil.copytree(fixture_dir, target)
    run = target / "jobs" / EXPERIMENT / RUN_ID / "job.json"
    payload = json.loads(run.read_text())
    payload["experiment_file"] = str(target / "experiment.yaml")
    run.write_text(json.dumps(payload))
    return target


def _problem(projection: QualityProjection):
    (experiment,) = projection.experiments
    (problem,) = experiment.problems
    return problem


def _edit_record(target: Path, job_id: str, trial_id: str, **changes: object) -> None:
    path = target / "jobs" / EXPERIMENT / job_id / "trial-records" / f"{trial_id}.json"
    record = json.loads(path.read_text())
    record.update(changes)
    path.write_text(json.dumps(record))


def _clone_run(target: Path, job_id: str, **manifest: object) -> Path:
    """A second run of the same trials (same snapshots), finishing later."""
    source = target / "jobs" / EXPERIMENT / RUN_ID
    other = target / "jobs" / EXPERIMENT / job_id
    shutil.copytree(source, other)
    payload = json.loads((other / "job.json").read_text())
    payload.update(job_id=job_id, finished_at="2026-10-01T00:00:00.000Z", **manifest)
    (other / "job.json").write_text(json.dumps(payload))
    for path in (other / "trial-records").glob("*.json"):
        record = json.loads(path.read_text())
        record["job_id"] = job_id
        path.write_text(json.dumps(record))
    return other


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
    fixture_dir: Path, projection: QualityProjection
) -> None:
    """C = mean per-checkpoint case pass rate (ADR-001 §2.4, the fork's C_p) and S agree
    with WS-J for every trial."""
    store = AssessmentStore(fixture_dir / "store")
    trials = fixture_dir / "jobs" / EXPERIMENT / RUN_ID / "trial-records"
    for detail in projection.run_details.values():
        record = TrialRecord.model_validate_json((trials / f"{detail.run.trial}.json").read_bytes())
        results = {
            ref: assessment.result
            for ref, key in detail.run.sources.items()
            if key and (assessment := store.get(key)) is not None
        }
        outcome = build_outcome(record, results, policy=SCB_CANONICAL)
        rates = {
            item.subject: item.value or 0.0
            for item in results["correctness@1"].observations
            if item.metric == "strict_pass_rate" and item.status is AssessStatus.OK
        }
        steps = record.trial.steps
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


def test_jev_decisions_attach_to_their_trial(projection: QualityProjection) -> None:
    labelled = projection.run_details[f"{RUN_PREFIX}b00-p0-baseline"].jev_decisions
    assert len(labelled) == 3
    assert {item.policy for item in labelled} == {"code-quality.rubric-smells"}
    assert projection.run_details[f"{RUN_PREFIX}b03-p0-baseline"].jev_decisions == ()


def test_jev_decisions_never_attach_to_a_run_they_may_not_belong_to(
    fixture_dir: Path, tmp_path: Path
) -> None:
    """A trial id shared by several runs attaches nowhere; a run-qualified name attaches once."""
    target = _copy(fixture_dir, tmp_path)
    _clone_run(target, "synthetic-run-2")
    audit = target / "store" / "jev-audit"
    for path in audit.rglob("*.jsonl"):
        lines = path.read_text().splitlines()
        rewritten = [
            line.replace('"trial":"b01-p0-baseline"', '"trial":"synthetic-run-2/b01-p0-baseline"')
            for line in lines
        ]
        path.write_text("\n".join(rewritten) + "\n")
    result = build_quality_projection(target / "jobs", target / "store")
    for job_id in (RUN_ID, "synthetic-run-2"):
        detail = result.run_details[f"{EXPERIMENT}.{job_id}.b00-p0-baseline"]
        assert detail.jev_decisions == ()
        assert any("not attached" in note and "2 runs share" in note for note in detail.notes)
    qualified = f"{EXPERIMENT}.synthetic-run-2.b01-p0-baseline"
    assert len(result.run_details[qualified].jev_decisions) == 3
    assert result.run_details[f"{RUN_PREFIX}b01-p0-baseline"].jev_decisions == ()


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
    assert erosion.verdict == "improved"
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


def test_assessments_are_matched_by_snapshot_content_not_trial_name(
    fixture_dir: Path, tmp_path: Path
) -> None:
    target = _copy(fixture_dir, tmp_path)
    source = target / "jobs" / EXPERIMENT / RUN_ID
    other = target / "jobs" / EXPERIMENT / "synthetic-run-2"
    other.mkdir()
    (other / "trial-records").mkdir()
    shutil.copy(source / "job.json", other / "job.json")
    record = json.loads((source / "trial-records" / "b00-p0-baseline.json").read_text())
    for step_result in record["step_results"]:
        step_result["snapshot"]["digest"] = "f" * 64
    (other / "trial-records" / "b00-p0-baseline.json").write_text(json.dumps(record))
    payload = json.loads((other / "job.json").read_text())
    payload["job_id"] = "synthetic-run-2"
    (other / "job.json").write_text(json.dumps(payload))

    result = build_quality_projection(target / "jobs", target / "store")
    unmatched = result.run_details[f"{EXPERIMENT}.synthetic-run-2.b00-p0-baseline"]
    assert unmatched.run.sources == {"code-quality@1": None, "correctness@1": None}
    assert unmatched.rows[0].cells["V"].status == "unavailable"
    matched = result.run_details[f"{RUN_PREFIX}b00-p0-baseline"]
    assert matched.run.sources["code-quality@1"] is not None
    problem = _problem(result)
    assert any("superseded" in item for item in problem.excluded)


def test_the_most_recently_logged_assessment_of_a_scorer_wins(
    fixture_dir: Path, tmp_path: Path
) -> None:
    target = _copy(fixture_dir, tmp_path)
    store = AssessmentStore(target / "store")
    job_id = f"{RUN_PREFIX}b00-p0-baseline"
    before = build_quality_projection(target / "jobs", target / "store").run_details[job_id]
    original = store.get(before.run.sources["correctness@1"] or "")
    assert original is not None
    replacement = original.model_copy(
        update={
            "config": {"progression": "continue"},
            "key": "e" * 64,
            "result": AssessResult(
                status=AssessStatus.OK,
                observations=(
                    *(
                        Observation(
                            metric="strict_pass_rate",
                            subject=row.checkpoint,
                            status=AssessStatus.OK,
                            value=0.5,
                        )
                        for row in before.rows
                    ),
                    Observation(
                        metric="solved_checkpoints",
                        subject="trajectory",
                        status=AssessStatus.OK,
                        value=2.0,
                    ),
                ),
            ),
        }
    )
    store.put(replacement)
    store.log(
        trial="b00-p0-baseline",
        key=replacement.key,
        scorer="correctness@1",
        outcome="added",
        stats=RunStats(duration_s=0),
    )
    after = build_quality_projection(target / "jobs", target / "store").run_details[job_id]
    assert after.run.sources["correctness@1"] == "e" * 64
    assert after.rows[0].cells["C"].value == 0.5
    # C is the mean case pass rate (0.5 everywhere), not solved_checkpoints / K.
    assert after.problem["C"].value == 0.5


def _lock(target: Path, headline_policy: str, experiment: str = EXPERIMENT) -> str:
    return write_synthetic_lock(
        target / "experiment.yaml",
        experiment=experiment,
        baseline_arm="baseline",
        metric=headline_policy,
    )


def _executed_lock(target: Path, job_id: str = RUN_ID) -> str:
    run = target / "jobs" / EXPERIMENT / job_id / "job.json"
    return str(json.loads(run.read_text())["lock_sha256"])


def test_the_locks_headline_policy_takes_precedence_over_the_default(
    fixture_dir: Path, tmp_path: Path
) -> None:
    target = _copy(fixture_dir, tmp_path)
    # A new headline metric is an assessment change: the relocked lock keeps the run's identity.
    assert _lock(target, "scb-canonical-oneshot@1") == _executed_lock(target)
    detail = build_quality_projection(target / "jobs", target / "store").run_details[
        f"{RUN_PREFIX}b00-p0-baseline"
    ]
    assert (detail.policy.ref, detail.policy.source) == ("scb-canonical-oneshot@1", "lock")
    assert detail.problem["R"].status == "not_applicable"
    assert detail.problem["S"].status == "ineligible"
    assert any("checkpoint_count_unsupported" in reason for reason in detail.policy_reasons)


@pytest.mark.parametrize("change", ["missing", "relocked"])
def test_without_the_runs_own_lock_there_is_no_baseline_and_no_paired_delta(
    fixture_dir: Path, tmp_path: Path, change: str
) -> None:
    """The baseline comes from the lock the run executed under, never from another experiment's."""
    target = _copy(fixture_dir, tmp_path)
    lock = target / "experiment.lock.json"
    if change == "missing":
        lock.unlink()
    else:
        _lock(target, "scb-canonical-oneshot@1", experiment=f"{EXPERIMENT}-redesigned")
    result = build_quality_projection(target / "jobs", target / "store")
    problem = _problem(result)
    assert problem.baseline_arm is None
    assert problem.paired == ()
    assert any("no baseline arm" in note for note in problem.notes)
    detail = result.run_details[f"{RUN_PREFIX}b00-p0-baseline"]
    assert any("lock unavailable" in note for note in detail.notes)
    assert (detail.policy.ref, detail.policy.source) == ("scb-canonical@1", "default_by_k")


def test_runs_under_different_locks_are_never_pooled(fixture_dir: Path, tmp_path: Path) -> None:
    target = _copy(fixture_dir, tmp_path)
    _clone_run(target, "synthetic-run-2", lock_sha256="0" * 64)
    result = build_quality_projection(target / "jobs", target / "store")
    by_lock = {item.lock_sha256: item for item in result.experiments}
    assert len(by_lock) == 2
    assert by_lock["0" * 64].jobs == ("synthetic-run-2",)
    original = next(item for lock, item in by_lock.items() if lock != "0" * 64)
    assert original.jobs == (RUN_ID,)
    assert not any("superseded" in item for item in original.problems[0].excluded)
    assert original.problems[0].baseline_arm == "baseline"


def test_truncated_trials_have_no_problem_outcome_and_stay_out_of_arm_statistics(
    fixture_dir: Path, tmp_path: Path
) -> None:
    """A max_checkpoints run never scores its unrun checkpoints as zero (F6)."""
    target = _copy(fixture_dir, tmp_path)
    path = target / "jobs" / EXPERIMENT / RUN_ID / "trial-records" / "b00-p0-guided.json"
    record = json.loads(path.read_text())
    step_results = [
        record["step_results"][0],
        *(
            {**step_result, "snapshot": None, "checkpoint_state": "not_attempted"}
            for step_result in record["step_results"][1:]
        ),
    ]
    _edit_record(
        target,
        RUN_ID,
        "b00-p0-guided",
        truncated_to=1,
        trial={**record["trial"], "steps": ["checkpoint_1"]},
        step_results=step_results,
    )
    # Without the lock, the default policy is chosen by the problem's K (every planned
    # step_result), not by the one step that ran: no one-shot default for a 5-checkpoint problem.
    (target / "experiment.lock.json").unlink()
    result = build_quality_projection(target / "jobs", target / "store")
    detail = result.run_details[f"{RUN_PREFIX}b00-p0-guided"]
    assert detail.run.truncated_to == 1
    assert (detail.policy.ref, detail.policy.source) == ("scb-canonical@1", "default_by_k")
    assert {cell.status for cell in detail.problem.values()} == {"truncated"}
    assert all("max_checkpoints" in (cell.reason or "") for cell in detail.problem.values())
    assert any("development job" in note for note in detail.notes)
    assert [row.checkpoint for row in detail.rows] == ["checkpoint_1"]
    problem = _problem(result)
    assert any("b00-p0-guided: development job" in item for item in problem.excluded)
    # Six guided attempts less the invalid one and the truncated one.
    assert problem.curves["guided"]["E"][0].n == 4


def test_agent_override_trials_render_but_stay_out_of_arm_statistics(
    fixture_dir: Path, tmp_path: Path
) -> None:
    target = _copy(fixture_dir, tmp_path)
    path = target / "jobs" / EXPERIMENT / RUN_ID / "trial-records" / "b00-p0-guided.json"
    record = json.loads(path.read_text())
    record["agent_override"] = "oracle"
    path.write_text(json.dumps(record))
    result = build_quality_projection(target / "jobs", target / "store")
    detail = result.run_details[f"{RUN_PREFIX}b00-p0-guided"]
    assert "agent override: oracle" in detail.notes
    problem = _problem(result)
    assert any("agent override oracle" in item for item in problem.excluded)
    erosion = next(item for item in problem.paired if item.component == "E")
    assert "attempt 0: guided not run" in erosion.excluded


def test_one_arm_has_no_head_to_head_and_small_samples_have_no_correlation(
    fixture_dir: Path, tmp_path: Path
) -> None:
    target = _copy(fixture_dir, tmp_path)
    trials = target / "jobs" / EXPERIMENT / RUN_ID / "trial-records"
    for path in trials.glob("*.json"):
        if "guided" in path.name or path.name[:3] not in {"b00", "b01"}:
            path.unlink()
    problem = _problem(build_quality_projection(target / "jobs", target / "store"))
    assert problem.arms == ("baseline",)
    assert problem.head_to_head is None
    assert problem.paired == ()
    assert all(
        item.r is None and item.reason == "n=2 < 3" for item in problem.sensitivity.correlations
    )


def test_cli_writes_a_projection_that_round_trips(fixture_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "quality.json"
    runs, store = str(fixture_dir / "jobs"), str(fixture_dir / "store")
    main(["--jobs-root", runs, "--store", store, "--output", str(output), "--synthetic"])
    loaded = QualityProjection.model_validate_json(output.read_bytes())
    assert loaded.synthetic
    assert len(loaded.runs) == 12
    assert str(fixture_dir) not in output.read_text()


def test_descriptive_statistics_boundaries() -> None:
    assert quantiles([]) is None
    assert quantiles([3.0]) == (3.0, 3.0, 3.0, 3.0, 3.0)
    assert quantiles([1.0, 2.0, 3.0, 4.0, 5.0]) == (1.0, 2.0, 3.0, 4.0, 5.0)
    assert pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
    assert pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)


def test_each_run_carries_its_trial_record_as_the_join_key_to_the_run_review(
    projection: QualityProjection, fixture_dir: Path
) -> None:
    for run in projection.runs:
        assert run.trial_record == f"jobs/{EXPERIMENT}/{RUN_ID}/trial-records/{run.trial}.json"
        assert (fixture_dir / run.trial_record).is_file()


def test_cli_reads_several_runs_roots_each_against_its_own_store(
    fixture_dir: Path, tmp_path: Path
) -> None:
    other = _copy(fixture_dir, tmp_path)
    moved = _clone_run(other, "synthetic-run-2")
    shutil.rmtree(other / "jobs" / EXPERIMENT / RUN_ID)
    output = tmp_path / "quality.json"
    main(
        [
            *("--jobs-root", str(fixture_dir / "jobs"), "--store", str(fixture_dir / "store")),
            *("--jobs-root", str(other / "jobs"), "--store", str(other / "store")),
            *("--output", str(output)),
        ]
    )
    loaded = QualityProjection.model_validate_json(output.read_bytes())
    assert {run.job for run in loaded.runs} == {RUN_ID, moved.name}
    assert len(loaded.runs) == 24
    assert all(run.score.status != "unassessed" for run in loaded.runs if run.job == moved.name)


def test_cli_refuses_unpaired_runs_roots_and_stores(fixture_dir: Path) -> None:
    runs = str(fixture_dir / "jobs")
    with pytest.raises(SystemExit):
        main(["--jobs-root", runs, "--jobs-root", runs, "--store", runs, "--output", "x.json"])


@pytest.mark.parametrize("source", ["flag", "run-copy"])
def test_a_relocked_runs_lock_is_found_by_flag_or_the_runs_own_copy(
    fixture_dir: Path, tmp_path: Path, source: str
) -> None:
    target = _copy(fixture_dir, tmp_path)
    lock = target / "experiment.lock.json"
    executed = tmp_path / "executed.lock.json"
    shutil.copy(lock, executed)
    _lock(target, "scb-canonical-oneshot@1", experiment=f"{EXPERIMENT}-redesigned")
    locks: tuple[Path, ...] = ()
    if source == "flag":
        locks = (executed,)
    else:
        shutil.copy(executed, target / "jobs" / EXPERIMENT / RUN_ID / "job.lock.json")

    result = build_quality_projection(target / "jobs", target / "store", locks=locks)

    problem = _problem(result)
    assert problem.baseline_arm == "baseline"
    assert problem.paired
    detail = result.run_details[f"{RUN_PREFIX}b00-p0-baseline"]
    assert detail.policy.source == "lock"


def test_the_missing_lock_note_says_why_not_that_the_experiment_file_is_gone(
    fixture_dir: Path, tmp_path: Path
) -> None:
    target = _copy(fixture_dir, tmp_path)
    _lock(target, "scb-canonical-oneshot@1", experiment=f"{EXPERIMENT}-redesigned")
    problem = _problem(build_quality_projection(target / "jobs", target / "store"))
    (note,) = [note for note in problem.notes if "no baseline arm" in note]
    assert "experiment file unavailable" not in note
    assert "but the run was executed under" in note


def test_a_baseline_with_no_counted_trials_is_named_in_the_notes(
    fixture_dir: Path, tmp_path: Path
) -> None:
    target = _copy(fixture_dir, tmp_path)
    for path in (target / "jobs" / EXPERIMENT / RUN_ID / "trial-records").glob("*-baseline.json"):
        record = json.loads(path.read_text())
        record["agent_override"] = "oracle"
        path.write_text(json.dumps(record))
    problem = _problem(build_quality_projection(target / "jobs", target / "store"))
    assert problem.baseline_arm == "baseline"
    assert "baseline" not in problem.arms
    assert problem.paired == ()
    assert any("baseline arm baseline has no counted trials" in note for note in problem.notes)


# ---- gate effort (gate-effort@1 + the guardrail-gate records) ---------------------------


def test_a_gated_run_shows_its_gate_rounds_and_failure_families(
    projection: QualityProjection,
) -> None:
    gated = projection.run_details[f"{RUN_PREFIX}b00-p0-guided"].gate
    assert gated is not None
    assert gated.source is not None
    assert [step.checkpoint for step in gated.steps] == [f"checkpoint_{n}" for n in range(1, 6)]
    unresolved = gated.steps[3]
    assert unresolved.termination == "rounds_exhausted"
    assert unresolved.cells["unresolved"].value == 1.0
    assert unresolved.cells["rounds_to_clean"].status == "not_applicable"
    assert [item.verdict for item in unresolved.rounds] == ["refused"] * 4
    first = unresolved.rounds[0]
    assert first.families is not None
    assert first.blocking == sum(first.families.values())
    assert first.log_path is not None
    assert first.log_path.endswith("steps/checkpoint_4/agent/guardrail-gate/gate_run_1.log")
    # The step cells are the stored Assessment's: the families sum over every run.
    totals = {family: cell.value for family, cell in unresolved.families.items()}
    assert sum(value or 0 for value in totals.values()) == sum(
        item.blocking or 0 for item in unresolved.rounds
    )
    assert gated.totals["unresolved_rate"].value == pytest.approx(0.2)
    # The ungated arm has no gate view at all (never zeros).
    assert projection.run_details[f"{RUN_PREFIX}b00-p0-baseline"].gate is None


def test_gate_records_without_an_assessment_or_with_a_broken_record_say_so(
    fixture_dir: Path, tmp_path: Path
) -> None:
    target = _copy(fixture_dir, tmp_path)
    for path in sorted((target / "store" / OBJECTS_DIR).glob("*/*.json")):
        if json.loads(path.read_text())["scorer"]["ref"] == "gate-effort@1":
            path.unlink()
    trace = target / "jobs" / EXPERIMENT / RUN_ID / "trials" / "b01-p0-guided" / "steps"
    (trace / "checkpoint_2" / "agent" / "guardrail-gate" / "guardrail-gate.json").write_text("[")

    rebuilt = build_quality_projection(target / "jobs", target / "store", synthetic=True)

    gate = rebuilt.run_details[f"{RUN_PREFIX}b01-p0-guided"].gate
    assert gate is not None
    assert gate.source is None
    assert gate.steps[0].cells["gate_runs"].status == "unassessed"
    assert gate.steps[1].rounds == ()
    assert any("checkpoint_2: gate record unreadable" in note for note in gate.notes)
    assert any("rescore its job with gate-effort@1" in note for note in gate.notes)
    assert gate.steps[3].rounds[0].verdict == "refused"
