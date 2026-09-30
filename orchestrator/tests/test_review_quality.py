"""Quality projection (plan K2): contract invariants, source matching and precedence."""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from raidar.analysis.assessed import build_outcome
from raidar.analysis.outcomes import HEADLINE_SCORE, SOLVE_RATE
from raidar.execution.models import TrajectoryRecord
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
from raidar.scorers.store import AssessmentStore
from raidar.spec.policies import SCB_CANONICAL

RUN_PREFIX = f"{EXPERIMENT}.{RUN_ID}."


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return generate_quality_fixture(tmp_path_factory.mktemp("quality") / "fx")


@pytest.fixture(scope="module")
def projection(fixture_dir: Path) -> QualityProjection:
    return build_quality_projection(fixture_dir / "runs", fixture_dir / "store", synthetic=True)


def _copy(fixture_dir: Path, tmp_path: Path) -> Path:
    target = tmp_path / "fx"
    shutil.copytree(fixture_dir, target)
    run = target / "runs" / EXPERIMENT / RUN_ID / "run.json"
    payload = json.loads(run.read_text())
    payload["experiment_file"] = str(target / "experiment.yaml")
    run.write_text(json.dumps(payload))
    return target


def _problem(projection: QualityProjection):
    (experiment,) = projection.experiments
    (problem,) = experiment.problems
    return problem


def _edit_record(target: Path, run_id: str, trial_id: str, **changes: object) -> None:
    path = target / "runs" / EXPERIMENT / run_id / "trajectories" / f"{trial_id}.json"
    record = json.loads(path.read_text())
    record.update(changes)
    path.write_text(json.dumps(record))


def _clone_run(target: Path, run_id: str, **manifest: object) -> Path:
    """A second run of the same trials (same snapshots), finishing later."""
    source = target / "runs" / EXPERIMENT / RUN_ID
    other = target / "runs" / EXPERIMENT / run_id
    shutil.copytree(source, other)
    payload = json.loads((other / "run.json").read_text())
    payload.update(run_id=run_id, finished_at="2026-10-01T00:00:00.000Z", **manifest)
    (other / "run.json").write_text(json.dumps(payload))
    for path in (other / "trajectories").glob("*.json"):
        record = json.loads(path.read_text())
        record["run_id"] = run_id
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
    """C = solved checkpoints / K (ADR-001 §2.4) and S agree with WS-J for every trial."""
    store = AssessmentStore(fixture_dir / "store")
    trajectories = fixture_dir / "runs" / EXPERIMENT / RUN_ID / "trajectories"
    for detail in projection.run_details.values():
        record = TrajectoryRecord.model_validate_json(
            (trajectories / f"{detail.run.trial}.json").read_bytes()
        )
        results = {
            ref: assessment.result
            for ref, key in detail.run.sources.items()
            if key and (assessment := store.get(key)) is not None
        }
        outcome = build_outcome(record, results, policy=SCB_CANONICAL)
        solved = next(
            item.value
            for item in results["correctness@1"].observations
            if item.metric == "solved_checkpoints"
        )
        assert solved is not None
        assert detail.problem["C"].value == pytest.approx(solved / len(record.trial.steps))
        assert detail.problem["C"].value == outcome.metrics[SOLVE_RATE].value
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
    arm, block, checkpoint = INELIGIBLE_H
    detail = projection.run_details[f"{RUN_PREFIX}b{block:02d}-p0-{arm}"]
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
    result = build_quality_projection(target / "runs", target / "store")
    for run_id in (RUN_ID, "synthetic-run-2"):
        detail = result.run_details[f"{EXPERIMENT}.{run_id}.b00-p0-baseline"]
        assert detail.jev_decisions == ()
        assert any("not attached" in note and "2 runs share" in note for note in detail.notes)
    qualified = f"{EXPERIMENT}.synthetic-run-2.b01-p0-baseline"
    assert len(result.run_details[qualified].jev_decisions) == 3
    assert result.run_details[f"{RUN_PREFIX}b01-p0-baseline"].jev_decisions == ()


def test_arm_statistics_leave_out_invalid_trials_and_pair_by_block(
    projection: QualityProjection,
) -> None:
    problem = _problem(projection)
    arm, block = INVALID
    assert problem.arms == ("baseline", "guided")
    assert any("infra-excluded" in item for item in problem.excluded)
    erosion = next(item for item in problem.paired if item.component == "E")
    assert erosion.n == 5
    assert f"block {block}: {arm} infra error" in erosion.excluded
    assert erosion.verdict == "improved"
    headline = next(item for item in problem.paired if item.component == "S")
    assert headline.n == 4
    assert headline.verdict == "insufficient"
    assert "pre-registered minimum" in headline.reason


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
    blocks = [
        detail
        for detail in projection.run_details.values()
        if detail.run.arm == "baseline" and detail.run.valid
    ]
    values = sorted(float(detail.rows[0].signals.lint_findings or 0) for detail in blocks)
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
    source = target / "runs" / EXPERIMENT / RUN_ID
    other = target / "runs" / EXPERIMENT / "synthetic-run-2"
    other.mkdir()
    (other / "trajectories").mkdir()
    shutil.copy(source / "run.json", other / "run.json")
    record = json.loads((source / "trajectories" / "b00-p0-baseline.json").read_text())
    for attempt in record["attempts"]:
        attempt["snapshot"]["digest"] = "f" * 64
    (other / "trajectories" / "b00-p0-baseline.json").write_text(json.dumps(record))
    payload = json.loads((other / "run.json").read_text())
    payload["run_id"] = "synthetic-run-2"
    (other / "run.json").write_text(json.dumps(payload))

    result = build_quality_projection(target / "runs", target / "store")
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
    run_id = f"{RUN_PREFIX}b00-p0-baseline"
    before = build_quality_projection(target / "runs", target / "store").run_details[run_id]
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
    after = build_quality_projection(target / "runs", target / "store").run_details[run_id]
    assert after.run.sources["correctness@1"] == "e" * 64
    assert after.rows[0].cells["C"].value == 0.5
    assert after.problem["C"].value == 2 / len(after.rows)


def _lock(target: Path, headline_policy: str) -> str:
    return write_synthetic_lock(
        target / "experiment.yaml",
        experiment=EXPERIMENT,
        baseline_arm="baseline",
        headline_policy=headline_policy,
    )


def _relock(target: Path, headline_policy: str, run_id: str = RUN_ID) -> str:
    digest = _lock(target, headline_policy)
    run = target / "runs" / EXPERIMENT / run_id / "run.json"
    run.write_text(json.dumps({**json.loads(run.read_text()), "lock_sha256": digest}))
    return digest


def test_the_locks_headline_policy_takes_precedence_over_the_default(
    fixture_dir: Path, tmp_path: Path
) -> None:
    target = _copy(fixture_dir, tmp_path)
    _relock(target, "scb-canonical-oneshot@1")
    detail = build_quality_projection(target / "runs", target / "store").run_details[
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
    """The baseline comes from the lock the run executed under, never from a newer lock."""
    target = _copy(fixture_dir, tmp_path)
    lock = target / "experiment.lock.json"
    if change == "missing":
        lock.unlink()
    else:
        _lock(target, "scb-canonical-oneshot@1")
    result = build_quality_projection(target / "runs", target / "store")
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
    result = build_quality_projection(target / "runs", target / "store")
    by_lock = {item.lock_sha256: item for item in result.experiments}
    assert len(by_lock) == 2
    assert by_lock["0" * 64].runs == ("synthetic-run-2",)
    original = next(item for lock, item in by_lock.items() if lock != "0" * 64)
    assert original.runs == (RUN_ID,)
    assert not any("superseded" in item for item in original.problems[0].excluded)
    assert original.problems[0].baseline_arm == "baseline"


def test_truncated_trajectories_have_no_problem_outcome_and_stay_out_of_arm_statistics(
    fixture_dir: Path, tmp_path: Path
) -> None:
    """A max_checkpoints run never scores its unrun checkpoints as zero (F6)."""
    target = _copy(fixture_dir, tmp_path)
    path = target / "runs" / EXPERIMENT / RUN_ID / "trajectories" / "b00-p0-guided.json"
    record = json.loads(path.read_text())
    attempts = [
        record["attempts"][0],
        *(
            {**attempt, "snapshot": None, "checkpoint_state": "not_attempted"}
            for attempt in record["attempts"][1:]
        ),
    ]
    _edit_record(
        target,
        RUN_ID,
        "b00-p0-guided",
        truncated_to=1,
        trial={**record["trial"], "steps": ["checkpoint_1"]},
        attempts=attempts,
    )
    # Without the lock, the default policy is chosen by the problem's K (every planned
    # attempt), not by the one step that ran: no one-shot default for a 5-checkpoint problem.
    (target / "experiment.lock.json").unlink()
    result = build_quality_projection(target / "runs", target / "store")
    detail = result.run_details[f"{RUN_PREFIX}b00-p0-guided"]
    assert detail.run.truncated_to == 1
    assert (detail.policy.ref, detail.policy.source) == ("scb-canonical@1", "default_by_k")
    assert {cell.status for cell in detail.problem.values()} == {"truncated"}
    assert all("max_checkpoints" in (cell.reason or "") for cell in detail.problem.values())
    assert any("development run" in note for note in detail.notes)
    assert [row.checkpoint for row in detail.rows] == ["checkpoint_1"]
    problem = _problem(result)
    assert any("b00-p0-guided: development run" in item for item in problem.excluded)
    # Six guided blocks less the invalid one and the truncated one.
    assert problem.curves["guided"]["E"][0].n == 4


def test_agent_override_trials_render_but_stay_out_of_arm_statistics(
    fixture_dir: Path, tmp_path: Path
) -> None:
    target = _copy(fixture_dir, tmp_path)
    path = target / "runs" / EXPERIMENT / RUN_ID / "trajectories" / "b00-p0-guided.json"
    record = json.loads(path.read_text())
    record["agent_override"] = "oracle"
    path.write_text(json.dumps(record))
    result = build_quality_projection(target / "runs", target / "store")
    detail = result.run_details[f"{RUN_PREFIX}b00-p0-guided"]
    assert "agent override: oracle" in detail.notes
    problem = _problem(result)
    assert any("agent override oracle" in item for item in problem.excluded)
    erosion = next(item for item in problem.paired if item.component == "E")
    assert "block 0: guided not run" in erosion.excluded


def test_one_arm_has_no_head_to_head_and_small_samples_have_no_correlation(
    fixture_dir: Path, tmp_path: Path
) -> None:
    target = _copy(fixture_dir, tmp_path)
    trajectories = target / "runs" / EXPERIMENT / RUN_ID / "trajectories"
    for path in trajectories.glob("*.json"):
        if "guided" in path.name or path.name[:3] not in {"b00", "b01"}:
            path.unlink()
    problem = _problem(build_quality_projection(target / "runs", target / "store"))
    assert problem.arms == ("baseline",)
    assert problem.head_to_head is None
    assert problem.paired == ()
    assert all(
        item.r is None and item.reason == "n=2 < 3" for item in problem.sensitivity.correlations
    )


def test_cli_writes_a_projection_that_round_trips(fixture_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "quality.json"
    runs, store = str(fixture_dir / "runs"), str(fixture_dir / "store")
    main(["--runs-root", runs, "--store", store, "--output", str(output), "--synthetic"])
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
