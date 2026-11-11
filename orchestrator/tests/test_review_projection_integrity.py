"""Both review producers apply the provenance and sealed-record gate to real run directories.

Which provenance differences refuse a pool is decided in memory (``check_review_jobs``,
``test_review_projection_contracts``); here each producer is shown to apply that gate to real
job directories and sealed trial records.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from raidar.engine.health.records_manifest import seal_trial
from raidar.evaluation.analysis.pooling import PoolingError
from raidar.evaluation.review.projection import merge_review_projections
from raidar.evaluation.review.quality.models import QualityProjection
from raidar.evaluation.review.quality.projection import build_quality_projection_from
from tests.capability_support import CAPABILITIES
from tests.review_fixtures.projection.generate import REVIEW_ONLY_DIR, generate_review_fixture

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def review_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("review-integrity")
    generate_review_fixture(root)
    return root


def _copy_job(root: Path, target: Path) -> Path:
    source = next((root / REVIEW_ONLY_DIR).glob("*/*/job.json")).parent
    shutil.copytree(source, target)
    return target


PARITY = {
    "image_id": "sha256:image",
    "seed_digest": "seed",
    "limits": {"agent_timeout_s": 1800.0},
    "network": [{"network_mode": "allowlist", "allowed_hosts": ["api.openai.com"]}],
}


def _modern(job: Path, **changes: object) -> None:
    """Give a historical fixture job what a job of the current epoch records, then seal it."""
    plan_path = job / "plan.json"
    plan = json.loads(plan_path.read_text())
    for path in (job / "trial-records").glob("*.json"):
        record = json.loads(path.read_text())
        arm = plan.setdefault("arms", {}).setdefault(record["trial"]["arm"], {})
        arm["capabilities"] = CAPABILITIES
        path.write_text(json.dumps({**record, "parity": PARITY}))
    plan_path.write_text(json.dumps(plan))
    manifest_path = job / "job.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(
        job_id=job.name,
        mechanics_epoch="mechanics-v1",
        runner={"dirty": False, "toolchain_sha256": "toolchain", "execution_sha256": "execution"},
        recipes={"pack": "pack", "graders": "grader"},
        effective_limits={"agent_s": 1800},
        execution_target={"name": "target-a", "docker": {"arch": "arm64"}},
        records_manifests={
            record.stem: seal_trial(job / "trials" / record.stem, record, job)
            for record in (job / "trial-records").glob("*.json")
        },
    )
    manifest.update(changes)
    manifest_path.write_text(json.dumps(manifest))


def _quality(
    jobs: Path, store: Path, *, synthetic: bool = False, locks: tuple[Path, ...] = ()
) -> QualityProjection:
    """The quality projection of one jobs root and its store."""
    return build_quality_projection_from([(jobs, store)], synthetic=synthetic, locks=locks)


def _project(pipeline: str, jobs: Path, store: Path):
    if pipeline == "quality":
        return _quality(jobs, store)
    return merge_review_projections(job_roots=[jobs], stores=[store])


@pytest.mark.parametrize("pipeline", ["main", "quality"])
@pytest.mark.parametrize("change", ["target"])
def test_review_refuses_incompatible_provenance_before_combining_jobs(
    review_fixture: Path, tmp_path: Path, pipeline: str, change: str
) -> None:
    one = _copy_job(review_fixture, tmp_path / "one")
    two = _copy_job(review_fixture, tmp_path / "two")
    _modern(one)
    _modern(two)
    manifest_path = two / "job.json"
    manifest = json.loads(manifest_path.read_text())
    if change == "target":
        manifest["execution_target"]["name"] = "target-b"
    elif change in {"toolchain", "execution"}:
        manifest["runner"][f"{change}_sha256"] = "another"
    elif change in {"pack", "grader"}:
        manifest["recipes"]["graders" if change == "grader" else "pack"] = "another"
    elif change == "limits":
        manifest["effective_limits"]["agent_s"] = 60
    elif change == "dirty":
        manifest["runner"]["dirty"] = True
    else:
        manifest["mechanics_epoch"] = "another-epoch"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(PoolingError, match="cannot be pooled"):
        _project(pipeline, tmp_path, review_fixture / "store")


@pytest.mark.parametrize("pipeline", ["main", "quality"])
def test_review_checks_a_single_jobs_records_and_preserves_unmeasured_historical_review(
    review_fixture: Path, tmp_path: Path, pipeline: str
) -> None:
    job = _copy_job(review_fixture, tmp_path / "one")
    historical = _project(pipeline, job, review_fixture / "store")
    if pipeline == "quality":
        assert all(
            any("provenance unmeasured" in note for note in detail.notes)
            for detail in historical.run_details.values()
        )
    else:
        assert all(
            any(note.category == "provenance-unmeasured" for note in detail.annotations)
            for detail in historical.run_details.values()
        )
    _modern(job)
    assert _project(pipeline, job, review_fixture / "store").runs
    record = next((job / "trial-records").glob("*.json"))
    record.write_text(record.read_text() + "\n")
    with pytest.raises(PoolingError, match="records manifest violation.*never overridable"):
        _project(pipeline, job, review_fixture / "store")


# ---- quality producer on a real run directory ----


@pytest.fixture(scope="module")
def quality_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from tests.review_fixtures.quality.generate import generate_quality_fixture

    return generate_quality_fixture(tmp_path_factory.mktemp("quality") / "fx")


def _quality_copy(fixture: Path, tmp_path: Path) -> Path:
    from tests.review_fixtures.quality.generate import EXPERIMENT, RUN_ID

    target = tmp_path / "fx"
    shutil.copytree(fixture, target)
    manifest = target / "jobs" / EXPERIMENT / RUN_ID / "job.json"
    payload = json.loads(manifest.read_text())
    payload["experiment_file"] = str(target / "experiment.yaml")
    manifest.write_text(json.dumps(payload))
    return target


def _quality_clone_run(target: Path, job_id: str) -> Path:
    """A second run of the same trials (same snapshots), finishing later."""
    from tests.review_fixtures.quality.generate import EXPERIMENT, RUN_ID

    other = target / "jobs" / EXPERIMENT / job_id
    shutil.copytree(target / "jobs" / EXPERIMENT / RUN_ID, other)
    manifest = json.loads((other / "job.json").read_text())
    manifest.update(job_id=job_id, finished_at="2026-10-01T00:00:00.000Z")
    (other / "job.json").write_text(json.dumps(manifest))
    for path in (other / "trial-records").glob("*.json"):
        record = json.loads(path.read_text())
        record["job_id"] = job_id
        path.write_text(json.dumps(record))
    return other


def test_quality_producer_projects_a_real_run_directory_as_the_in_memory_design_does(
    quality_fixture: Path,
) -> None:
    """Real stores, keys, snapshots, gate records and audit log read as the in-memory inputs do."""
    from raidar.evaluation.review.quality.projection import assemble_projection, review_trials
    from tests.review_fixtures.quality.generate import synthetic_quality

    disk = _quality(quality_fixture / "jobs", quality_fixture / "store", synthetic=True)
    design = synthetic_quality()
    memory = assemble_projection(
        review_trials(design.trials, design.find, design.audit(), design.read_gate),
        generated_from="memory",
        synthetic=True,
        integrity={},
    )
    assert set(disk.run_details) == set(memory.run_details)
    assert [item.problems for item in disk.experiments] == [
        item.problems for item in memory.experiments
    ]
    for run_id, detail in disk.run_details.items():
        expected = memory.run_details[run_id]
        assert all(key is not None for key in detail.run.sources.values())
        assert (quality_fixture / detail.run.trial_record).is_file()
        assert (detail.policy, detail.rows, detail.problem, detail.evidence) == (
            expected.policy,
            expected.rows,
            expected.problem,
            expected.evidence,
        )
        assert (detail.exclusions, detail.jev_decisions) == (
            expected.exclusions,
            expected.jev_decisions,
        )
        if expected.gate is None:
            assert detail.gate is None
        else:
            assert detail.gate is not None
            assert detail.gate.source is not None
            assert detail.gate.model_copy(update={"source": None}) == expected.gate.model_copy(
                update={"source": None}
            )


@pytest.mark.parametrize("changed", ["config", "verifier"])
def test_only_the_locked_exact_assessment_is_used_even_when_a_newer_one_has_the_same_snapshots(
    quality_fixture: Path, tmp_path: Path, changed: str
) -> None:
    from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation, RunStats
    from raidar.scoring.framework.store import AssessmentStore, assessment_key
    from tests.review_fixtures.quality.generate import EXPERIMENT, RUN_ID

    target = _quality_copy(quality_fixture, tmp_path)
    store = AssessmentStore(target / "store")
    job_id = f"{EXPERIMENT}.{RUN_ID}.b00-p0-baseline"
    before = _quality(target / "jobs", target / "store").run_details[job_id]
    original = store.get(before.run.sources["correctness@10"] or "")
    assert original is not None
    if changed == "config":
        updates: dict[str, object] = {"config": {**original.config, "new_setting": True}}
    else:
        inputs = json.loads(json.dumps(original.inputs))
        inputs["steps"][0]["oracle_record"] = "e" * 64
        updates = {"inputs": inputs}
    replacement = original.model_copy(update=updates)
    other = AssessResult(
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
    )
    key = assessment_key(
        replacement.scorer,
        config=replacement.config,
        tool_lock=replacement.tool_lock,
        inputs=replacement.inputs,
    )
    store.put(replacement.model_copy(update={"key": key, "result": other}))
    store.log(
        trial="b00-p0-baseline",
        key=key,
        scorer="correctness@10",
        outcome="added",
        stats=RunStats(duration_s=0),
    )
    after = _quality(target / "jobs", target / "store").run_details[job_id]
    assert after.run.sources["correctness@10"] == original.key
    assert after.problem["C"] == before.problem["C"]
    store.path_for(original.key).unlink()
    missing = _quality(target / "jobs", target / "store").run_details[job_id]
    assert missing.run.sources["correctness@10"] is None
    assert missing.problem["C"].value is None


def test_relocking_assessment_settings_requires_the_new_exact_assessment(
    quality_fixture: Path, tmp_path: Path
) -> None:
    from raidar.design.experiment.lock import seal_lock
    from raidar.engine.job.assessment import lock_rescore_config
    from raidar.scoring.framework.store import AssessmentStore
    from tests.review_fixtures.quality.generate import EXPERIMENT, RUN_ID
    from tests.review_fixtures.quality.store import FixtureAssessments

    target = _quality_copy(quality_fixture, tmp_path)
    run_id = f"{EXPERIMENT}.{RUN_ID}.b00-p0-baseline"
    before = _quality(target / "jobs", target / "store").run_details[run_id]
    store = AssessmentStore(target / "store")
    original = store.get(before.run.sources["correctness@10"] or "")
    assert original is not None
    lock_path = target / "experiment.lock.json"
    lock = json.loads(lock_path.read_text())
    lock["spec"]["assessment"]["inventory_policy"] = {"generated_globs": ["generated/**"]}
    for derived in ("sections", "lock_sha256", "document_sha256"):
        lock.pop(derived)
    lock = seal_lock(lock)
    lock_path.write_text(json.dumps(lock))
    unassessed = _quality(target / "jobs", target / "store").run_details[run_id]
    assert unassessed.run.sources["correctness@10"] is None
    trial = target / "jobs" / EXPERIMENT / RUN_ID / "trials" / "b00-p0-baseline"
    exact = FixtureAssessments(lock_rescore_config(lock)).assessment(
        "correctness@10", trial, original.result
    )
    store.put(exact)
    assessed = _quality(target / "jobs", target / "store").run_details[run_id]
    assert assessed.run.sources["correctness@10"] == exact.key


def test_a_lock_named_by_flag_is_read_from_its_file_for_runs_whose_own_lock_moved_on(
    quality_fixture: Path, tmp_path: Path
) -> None:
    from tests.review_fixtures.quality.generate import ARMS, CHECKPOINTS, EXPERIMENT, PROBLEM
    from tests.review_fixtures.quality.lock import write_synthetic_lock

    target = _quality_copy(quality_fixture, tmp_path)
    executed = tmp_path / "executed.lock.json"
    shutil.copy(target / "experiment.lock.json", executed)
    write_synthetic_lock(
        target / "experiment.yaml",
        experiment=f"{EXPERIMENT}-redesigned",
        baseline_arm=ARMS[0],
        metric="scb-canonical-oneshot@1",
        problem=PROBLEM,
        checkpoints=len(CHECKPOINTS),
    )
    plain = _quality(target / "jobs", target / "store")
    flagged = _quality(target / "jobs", target / "store", locks=(executed,))
    assert plain.experiments[0].problems[0].baseline_arm is None
    assert all(key is None for run in plain.runs for key in run.sources.values())
    assert flagged.experiments[0].problems[0].baseline_arm == "baseline"
    assert flagged.experiments[0].problems[0].paired
    assert all(key is not None for run in flagged.runs for key in run.sources.values())


def test_gate_records_on_disk_without_an_assessment_or_with_a_broken_record_say_so(
    quality_fixture: Path, tmp_path: Path
) -> None:
    from raidar.scoring.framework.store import OBJECTS_DIR
    from tests.review_fixtures.quality.generate import EXPERIMENT, RUN_ID

    target = _quality_copy(quality_fixture, tmp_path)
    for path in sorted((target / "store" / OBJECTS_DIR).glob("*/*.json")):
        if json.loads(path.read_text())["scorer"]["ref"] == "verification-effort@1":
            path.unlink()
    trial = target / "jobs" / EXPERIMENT / RUN_ID / "trials" / "b01-p0-guided" / "steps"
    (trial / "checkpoint_2" / "agent" / "workflow" / "workflow.json").write_text("[")

    rebuilt = _quality(target / "jobs", target / "store", synthetic=True)

    gate = rebuilt.run_details[f"{EXPERIMENT}.{RUN_ID}.b01-p0-guided"].gate
    assert gate is not None
    assert gate.source is None
    assert gate.steps[0].cells["gate_runs"].status == "unassessed"
    assert gate.steps[0].rounds
    assert gate.steps[1].rounds == ()
    assert any(note.startswith("checkpoint_2: gate record unreadable") for note in gate.notes)


def test_runs_found_by_discovery_share_no_trial_identity_and_non_runs_are_ignored(
    quality_fixture: Path, tmp_path: Path
) -> None:
    from tests.review_fixtures.quality.generate import EXPERIMENT, RUN_ID

    target = _quality_copy(quality_fixture, tmp_path)
    other = _quality_clone_run(target, "synthetic-run-2")
    for name, text in (("not-a-run", '{"kind": "something-else"}'), ("broken", "[")):
        (target / "jobs" / name).mkdir()
        (target / "jobs" / name / "job.json").write_text(text)

    result = _quality(target / "jobs", target / "store")

    assert {run.job for run in result.runs} == {RUN_ID, other.name}
    assert len(result.runs) == 24
    assert all(run.score.status != "unassessed" for run in result.runs)
    for job_id in (RUN_ID, other.name):
        detail = result.run_details[f"{EXPERIMENT}.{job_id}.b00-p0-baseline"]
        assert detail.jev_decisions == ()
        assert any("not attached" in note for note in detail.notes)
    (experiment,) = result.experiments
    assert experiment.jobs == (RUN_ID, other.name)


def test_quality_cli_reads_several_runs_roots_each_against_its_own_store(
    quality_fixture: Path, tmp_path: Path
) -> None:
    from raidar.evaluation.review.quality.models import QualityProjection
    from raidar.evaluation.review.quality.projection import main
    from tests.review_fixtures.quality.generate import EXPERIMENT, RUN_ID

    other = _quality_copy(quality_fixture, tmp_path)
    moved = _quality_clone_run(other, "synthetic-run-2")
    shutil.rmtree(other / "jobs" / EXPERIMENT / RUN_ID)
    output = tmp_path / "quality.json"
    main(
        [
            *("--jobs-root", str(quality_fixture / "jobs")),
            *("--store", str(quality_fixture / "store")),
            *("--jobs-root", str(other / "jobs")),
            *("--store", str(other / "store")),
            *("--output", str(output)),
            "--synthetic",
        ]
    )
    loaded = QualityProjection.model_validate_json(output.read_bytes())
    assert loaded.synthetic
    assert {run.job for run in loaded.runs} == {RUN_ID, moved.name}
    assert len(loaded.runs) == 24
    assert all(run.score.status != "unassessed" for run in loaded.runs if run.job == moved.name)
    assert str(quality_fixture) not in output.read_text()
