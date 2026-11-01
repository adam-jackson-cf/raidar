"""The experiment-epoch review projection over real run directories and Assessment stores.

What the projection decides from a run's records, Assessments and files is tested in memory
(``test_review_projection_contracts``). Here the real directory seams are shown to feed it:
discovery, locks beside experiment files, the Assessment store's keys, the trial context a
rescore reads, and the CLI over a job tree.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from raidar.contract.review import ReviewProjectionArtifact, ReviewRunRecord
from raidar.evaluation.analysis.sources import AnalysisInputError
from raidar.evaluation.review.projection import main, merge_review_projections
from raidar.evaluation.review.projection.experiment_epoch import project_sources
from raidar.evaluation.review.projection.merge import assemble_projection, project_experiment_epoch
from raidar.evaluation.review.projection.sources import LockIndex, load_job
from tests.review_fixtures.projection.generate import (
    JOBS_DIR,
    REVIEW_ONLY_DIR,
    MemoryReview,
    generate_review_fixture,
    memory_review_fixture,
)

pytestmark = pytest.mark.slow

PASSING = "synthetic-bugfix-gpt-5.5-medium-01"


@pytest.fixture(scope="module")
def fixture_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("review-fixture")
    generate_review_fixture(root)
    return root


@pytest.fixture(scope="module")
def memory() -> MemoryReview:
    return memory_review_fixture()


def _copy_run(fixture_root: Path, tmp_path: Path, experiment: str) -> Path:
    """A copy of a fixture run whose manifest names the copy's own experiment file and lock."""
    run = tmp_path / "jobs" / experiment / experiment
    shutil.copytree(fixture_root / "jobs" / experiment / experiment, run)
    manifest = json.loads((run / "job.json").read_text())
    (run / "job.json").write_text(
        json.dumps({**manifest, "experiment_file": str(run / "experiment.yaml")})
    )
    return run


def _outcome(run: ReviewRunRecord) -> dict[str, Any]:
    """What a review shows of a run, apart from where its files sit."""
    return {
        "id": run.id,
        "problem": run.problem,
        "arm": run.arm,
        "agent_spec": run.agent_spec,
        "status": run.status,
        "valid": run.valid,
        "unscored": run.unscored,
        "composite": None if run.composite_score is None else round(run.composite_score, 9),
        "quality": None if run.quality_score is None else round(run.quality_score, 9),
        "diagnostic": None if run.diagnostic_score is None else round(run.diagnostic_score, 9),
        "failed_gates": run.failed_gates,
        "metric_scores": run.metric_scores,
        "spans": run.span_count,
        "tokens": (run.total_input_tokens, run.total_output_tokens),
        "duration_ms": run.duration_ms,
        "started_at": run.started_at,
    }


def test_run_directories_project_exactly_as_the_same_runs_held_in_memory(
    fixture_root: Path, memory: MemoryReview
) -> None:
    on_disk = merge_review_projections(
        job_roots=[fixture_root / JOBS_DIR, fixture_root / REVIEW_ONLY_DIR],
        stores=[fixture_root / "store"],
    )
    in_memory = assemble_projection(project_sources(memory.sources, memory, memory.read), [])
    assert {run.id: _outcome(run) for run in on_disk.runs} == {
        run.id: _outcome(run) for run in in_memory.runs
    }
    assert {
        (record.experiment_id, record.arm): record.aggregate["run_count_scored"]
        for record in on_disk.experiments
    } == {
        (record.experiment_id, record.arm): record.aggregate["run_count_scored"]
        for record in in_memory.experiments
    }


def test_the_cli_projects_real_run_roots_and_skips_a_job_without_trial_records(
    fixture_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pending = tmp_path / "jobs/pending/r1"
    pending.mkdir(parents=True)
    source = fixture_root / "jobs/synthetic-skill/synthetic-skill"
    for name in ("job.json", "plan.json", "experiment.lock.json"):
        shutil.copy(source / name, pending / name)
    output = tmp_path / "out" / "review.json"
    main(
        [
            "--jobs-root",
            str(fixture_root / "jobs"),
            "--jobs-root",
            str(tmp_path / "jobs"),
            "--store",
            str(fixture_root / "store"),
            "--output",
            str(output),
        ]
    )
    artifact = ReviewProjectionArtifact.model_validate_json(output.read_text())
    unfiltered = merge_review_projections(
        job_roots=[fixture_root / "jobs"], stores=[fixture_root / "store"]
    )
    assert set(artifact.run_details) == set(unfiltered.run_details)
    assert "Skipping run without trial records" in capsys.readouterr().err


def test_run_without_its_lock_is_skipped_until_the_lock_is_given(
    fixture_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _copy_run(fixture_root, tmp_path, "synthetic-bugfix")
    shutil.copytree(
        fixture_root / "jobs/synthetic-skill/synthetic-skill",
        tmp_path / "jobs/synthetic-skill/synthetic-skill",
    )
    moved = tmp_path / "elsewhere.lock.json"
    (run / "experiment.lock.json").rename(moved)
    skipped = project_experiment_epoch([tmp_path / "jobs"])
    assert "pass it with --lock" in capsys.readouterr().err
    assert skipped.details  # the other run is still projected
    assert not any("bugfix" in detail.run.id for detail in skipped.details)
    with pytest.raises(AnalysisInputError, match="pass it with --lock"):
        load_job(run, LockIndex())
    epoch = project_experiment_epoch([tmp_path / "jobs/synthetic-bugfix"], locks=[moved])
    assert len(epoch.details) == 5 * 3


def test_a_lock_whose_body_was_edited_beside_the_experiment_file_is_refused(
    fixture_root: Path, tmp_path: Path
) -> None:
    run = _copy_run(fixture_root, tmp_path, "synthetic-bugfix")
    lock_path = run / "experiment.lock.json"
    lock = json.loads(lock_path.read_text())
    lock["assessment"]["metric"]["ref"] = "scb-canonical-oneshot@1"
    lock_path.write_text(json.dumps(lock))
    assert not project_experiment_epoch([tmp_path / "jobs"]).details


def test_trial_inputs_a_rescore_cannot_read_are_reported_per_scorer(
    fixture_root: Path, tmp_path: Path
) -> None:
    runs = tmp_path / "jobs"
    shutil.copytree(fixture_root / "jobs/synthetic-skill", runs / "synthetic-skill")
    shutil.copytree(fixture_root / "bundles", tmp_path / "bundles")
    moved = tmp_path / "bundles/synthetic-skill/oracle-bundle.json"
    moved.write_text(moved.read_text().replace("python:3.12-slim", "python:3.13-slim"))
    for config in runs.glob("synthetic-skill/synthetic-skill/trials/*/config.json"):
        config.write_text(config.read_text().replace(str(fixture_root), str(tmp_path)))
    for record in runs.glob(
        "synthetic-skill/synthetic-skill/trials/*/raidar-oracle/steps/*/record.json"
    ):
        record.write_text(record.read_text().replace(str(fixture_root), str(tmp_path)))
    run = merge_review_projections(job_roots=[runs], stores=[fixture_root / "store"]).runs[0]
    assert run.unscored
    assert any("trial inputs cannot be assessed" in reason for reason in run.unscored_reasons)


def test_an_empty_store_names_the_lock_whose_settings_key_the_assessment(
    fixture_root: Path, tmp_path: Path
) -> None:
    artifact = merge_review_projections(
        job_roots=[fixture_root / "jobs"], stores=[tmp_path / "empty-store"]
    )
    run = artifact.run_details[PASSING].run
    lock = (fixture_root / "jobs/synthetic-bugfix/synthetic-bugfix/experiment.lock.json").resolve()
    hints = [reason for reason in run.unscored_reasons if "raidar assess --rescore" in reason]
    assert hints
    assert all(f"--lock {lock}" in hint for hint in hints)


def test_following_the_unassessed_hint_stores_the_assessment_the_review_finds(
    fixture_root: Path, tmp_path: Path
) -> None:
    from click.testing import CliRunner

    from raidar.cli.assess import assess

    job_dir = _copy_run(fixture_root, tmp_path, "synthetic-bugfix")
    config = job_dir / "assessment-config.yaml"
    config.write_text(
        config.read_text().replace(
            "scorers: {}", "scorers:\n  correctness@10:\n    progression: continue"
        )
    )
    runs, store = tmp_path / "jobs", tmp_path / "store"
    before = merge_review_projections(job_roots=[runs], stores=[store])
    [hint] = [
        reason
        for reason in before.run_details[PASSING].run.unscored_reasons
        if "correctness@10" in reason and "raidar assess" in reason
    ]
    command = hint.split("`")[1].replace("<store>", str(store)).split()
    result = CliRunner().invoke(assess, command[2:])
    assert result.exit_code == 0, result.output
    after = merge_review_projections(job_roots=[runs], stores=[store])
    scoring = json.loads(
        next(
            span for span in after.run_details[PASSING].spans if span.name == "scoring"
        ).output_payload
        or "{}"
    )
    assert "correctness@10" in scoring["assessments"]
    assert "correctness@10" not in scoring["not_assessed"]
