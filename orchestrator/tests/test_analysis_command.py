"""``raidar analyze`` over stored jobs and a real Assessment store (slow seams).

Trials are written with host-side verifier records, rescored with the real
``correctness@10`` and ``resource-usage@8`` scorers, and analysed through the
CLI. The analysis must find each trial's Assessments by recomputing the keys
``raidar assess --rescore`` wrote, and must leave jobs and the store untouched.
Verdict, threshold, lock-refusal and loading decisions are fast tests
(``test_analysis_primary_outcome``, ``test_analysis_outcomes``, ``test_analysis_effects``).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.contract.references import ResolverSet
from raidar.contract.trial import CheckpointState, StepResult, TrialRecord
from raidar.design.experiment.loader import load_experiment
from raidar.design.experiment.lock import build_lock, render_lock, stale_sections
from raidar.design.problem.store import ROOT_ENV
from raidar.engine.health.records_manifest import RECORDS_MANIFEST, seal_trial
from raidar.engine.job.assessment import lock_rescore_config
from raidar.evaluation.analysis.pooling import PoolingError, check_pooling
from raidar.evaluation.analysis.sources import JobDir
from raidar.grading.models import CaseStatus
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.catalog.outcomes import SOLVE_RATE_ERRATA
from raidar.scoring.framework.metrics import MetricResolver
from raidar.scoring.framework.rescore import rescore_path
from raidar.scoring.framework.store import AssessmentStore
from tests.analysis_support import RECORD_STEPS as STEPS
from tests.analysis_support import analysis_lock as _lock
from tests.analysis_support import trial_record
from tests.experiment_spec_support import experiment_document, fixture_resolvers, write_experiment
from tests.scorer_support import (
    APP_V1,
    APP_V2,
    StepFixture,
    tree_state,
    write_toy_bundle,
    write_trial,
)

pytestmark = pytest.mark.slow

PASS, FAIL = CaseStatus.PASSED, CaseStatus.FAILED
# Guided solves both checkpoints except in attempt 3; baseline always fails checkpoint 2.
SOLVES = {
    "baseline": [(PASS, FAIL)] * 5,
    "guided": [(PASS, PASS)] * 3 + [(PASS, FAIL), (PASS, PASS)],
}
COST = {"baseline": 0.01, "guided": 0.03}

# Analysis looks Assessments up under the lock's assessment settings.
LOCKED_SETTINGS = lock_rescore_config(_lock(["correctness@10"]))


def _record(
    trial_id: str, arm: str, attempt: int, *, valid: bool = True, truncated_to: int | None = None
) -> TrialRecord:
    results = tuple(
        StepResult(
            trial_id=trial_id,
            arm=arm,
            attempt=attempt,
            problem="scb/toy@1",
            index=index,
            step=step,
            checkpoint=step,
            checkpoint_state=CheckpointState.SOLVED
            if SOLVES[arm][attempt][index] == PASS
            else CheckpointState.FAILED,
        )
        for index, step in enumerate(STEPS)
        if truncated_to is not None
    )
    return trial_record(
        trial_id, arm, attempt, valid=valid, truncated_to=truncated_to, step_results=results
    )


def _add_cost(trial_dir: Path, cost: float) -> None:
    result_path = trial_dir / "result.json"
    result = json.loads(result_path.read_text())
    for step in result["step_results"]:
        step["agent_result"]["cost_usd"] = cost
        step["agent_result"]["n_output_tokens"] = 10
    result_path.write_text(json.dumps(result, indent=2) + "\n")


def _write_run(
    root: Path,
    lock: dict[str, Any],
    *,
    name: str = "run-1",
    max_checkpoints: int | None = None,
    record_truncation: bool = True,
    outcomes: dict[str, list[tuple[CaseStatus, CaseStatus]]] | None = None,
) -> Path:
    job_dir = root / name
    trials = []
    for arm, solves in (outcomes or SOLVES).items():
        for attempt, (first, second) in enumerate(solves):
            trial_id = f"toy-{arm}-b{attempt}"
            trial_dir = write_trial(
                job_dir / "trials" / trial_id,
                [StepFixture(STEPS[0], APP_V1, (first,)), StepFixture(STEPS[1], APP_V2, (second,))],
            )
            _add_cost(trial_dir, COST[arm])
            truncated = max_checkpoints if record_truncation else None
            record = _record(trial_id, arm, attempt, truncated_to=truncated)
            path = job_dir / "trial-records" / f"{trial_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(record.model_dump_json())
            trials.append({**record.trial.model_dump(mode="json"), "selected": True})
    options = {"max_checkpoints": max_checkpoints, "agent_override": None}
    plan = {"kind": "raidar-plan", "options": options, "trials": trials}
    (job_dir / "plan.json").write_text(json.dumps(plan))
    manifest = {"kind": "raidar-job", "job_id": name, "lock_sha256": lock["lock_sha256"]}
    (job_dir / "job.json").write_text(json.dumps(manifest))
    (job_dir / "assessment-config.yaml").write_text("scorers: {}\n")
    return job_dir


@pytest.fixture
def pilot(tmp_path: Path) -> tuple[Path, Path, Path]:
    lock = _lock(["correctness@10", "code-quality@9", "resource-usage@8"])
    lock_path = tmp_path / "pilot.lock.json"
    lock_path.write_text(json.dumps(lock))
    job_dir = _write_run(tmp_path, lock)
    store = tmp_path / "store"
    rescore_path(
        job_dir / "trials",
        ("correctness@10", "resource-usage@8"),
        AssessmentStore(store),
        default_scorer_registry(),
        config=LOCKED_SETTINGS.scorers,
    )
    return job_dir, store, lock_path


def _analyze(job_dir: Path | list[Path], store: Path, lock_path: Path, output: Path, *extra: str):
    job_dirs = job_dir if isinstance(job_dir, list) else [job_dir]
    return CliRunner().invoke(
        main,
        [
            "analyze",
            *map(str, job_dirs),
            "--store",
            str(store),
            "--lock",
            str(lock_path),
            "--output",
            str(output),
            "--bootstrap-samples",
            "500",
            *extra,
        ],
    )


def _effect(report: dict[str, Any], metric: str) -> dict[str, Any]:
    (comparison,) = report["comparisons"]
    return next(effect for effect in comparison["effects"] if effect["metric"] == metric)


def test_analyze_reports_stored_assessments_without_touching_inputs(
    pilot: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    job_dir, store, lock_path = pilot
    before = (tree_state(job_dir), tree_state(store))
    output = tmp_path / "analysis"
    result = _analyze(
        job_dir,
        store,
        lock_path,
        output,
        "--min-effect",
        "solve_rate=0.25",
        "--max-exclusion-difference",
        "0.5",
    )
    assert result.exit_code == 0, result.output
    assert (tree_state(job_dir), tree_state(store)) == before
    report = json.loads((output / "analysis.json").read_text())
    assert report["baseline_arm"] == "baseline"
    assert report["settings"]["seed"] == 11
    solve = _effect(report, "solve_rate")
    assert (solve["n_pairs"], solve["verdict"]) == (5, "inconclusive")
    assert solve["mean_delta"] == pytest.approx(0.4)
    assert solve["cost"]["delta_mean"] == pytest.approx(0.04)  # two steps x 0.02 USD
    assert solve["sample_size"]["delta_source"] == "configured"
    assert solve["sample_size"]["recommended_pairs"] == 7  # ceil(7.84 * (0.2236 / 0.25)^2)
    # The default primary of a continue experiment is the solvable-set solve rate; this lock
    # pins no solvable set, so it is unavailable (never zero) with the raw rate beside it.
    assert report["primary_metric"] == "solve_rate_solvable"
    primary = report["comparisons"][0]["primary"]
    assert (primary["metric"], primary["verdict"]) == ("solve_rate_solvable", "insufficient")
    assert report["comparisons"][0]["primary_raw"]["metric"] == "solve_rate"
    composite = _effect(report, "headline_score")
    assert composite["exclusions"]["unassessed"] == 5
    assert composite["verdict"] == "insufficient"
    assert report["dev_job"] is None
    final = _effect(report, "final_solved")
    assert final["mcnemar"]["treatment_only"] == 4
    assert final["verdict"] == "inconclusive"


def test_rescore_and_analysis_follow_the_lock_not_the_runs_execution_time_file(
    tmp_path: Path,
) -> None:
    lock = _lock(["correctness@10", "code-quality@9", "resource-usage@8"])
    lock_path = tmp_path / "pilot.lock.json"
    lock_path.write_text(json.dumps(lock))
    job_dir = _write_run(tmp_path, lock)  # its assessment-config.yaml says `scorers: {}`
    store = tmp_path / "store"
    rescored = CliRunner().invoke(
        main,
        [
            "assess",
            "--rescore",
            str(job_dir / "trials"),
            "--scorer",
            "correctness@10",
            "--store",
            str(store),
            "--lock",
            str(lock_path),
        ],
    )
    assert rescored.exit_code == 0, rescored.output
    assert json.loads(rescored.output)["config"] == str(lock_path)

    result = _analyze(job_dir, store, lock_path, tmp_path / "out")
    assert result.exit_code == 0, result.output
    report = json.loads((tmp_path / "out" / "analysis.json").read_text())
    assert _effect(report, "solve_rate")["n_pairs"] == 5


def _move_bundles_into_problem_store(job_dir: Path, tmp_path: Path) -> Path:
    """Take the trials' bundles from their recorded roots; one becomes the store's oracle."""
    problems = tmp_path / "problems"
    oracle = problems / "store" / "scb" / "toy" / "1" / "unpinned" / "oracle"
    oracle.parent.mkdir(parents=True)
    bundles = sorted((job_dir / "trials").glob("*-bundle"))
    bundles[0].rename(oracle)
    for bundle in bundles[1:]:
        shutil.rmtree(bundle)
    manifest = problems / "registry" / "scb" / "toy" / "1.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "ref": "scb/toy@1",
                "mode": "multi_checkpoint",
                "checkpoints": len(STEPS),
                "steps": [
                    {
                        "name": step,
                        "checkpoint": step,
                        "instruction_sha256": "fixture",
                        "test_files": [f"test_{step}.py"],
                    }
                    for step in STEPS
                ],
                "source": {
                    "dataset": "scb",
                    "url": "https://example.test/scb",
                    "commit": "1" * 40,
                    "problem": "toy",
                    "problem_tree": "fixture",
                    "converter_tree": "fixture",
                    "converted_task_sha256": "fixture",
                },
                "pack": {"id": "python", "version": 1, "image": "fixture"},
                "task_sha256": "fixture",
                "bundle_sha256": "fixture",
                "references_sha256": "fixture",
            }
        )
    )
    return problems


def test_a_moved_oracle_bundle_is_read_from_the_problem_store_by_the_trials_problem(
    pilot: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir, store, lock_path = pilot
    problems = _move_bundles_into_problem_store(job_dir, tmp_path)

    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "empty-store"))
    absent = _analyze(job_dir, store, lock_path, tmp_path / "absent")
    assert absent.exit_code != 0
    assert "cannot be assessed" in absent.output
    assert "scb/toy@1" in absent.output
    assert "--bundle-search" not in absent.output

    monkeypatch.setenv(ROOT_ENV, str(problems))
    found = _analyze(job_dir, store, lock_path, tmp_path / "found")
    assert found.exit_code == 0, found.output
    report = json.loads((tmp_path / "found" / "analysis.json").read_text())
    assert _effect(report, "solve_rate")["n_pairs"] == 5


def test_a_problem_store_bundle_with_another_digest_is_refused(
    pilot: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir, store, lock_path = pilot
    problems = _move_bundles_into_problem_store(job_dir, tmp_path)
    oracle = problems / "store" / "scb" / "toy" / "1" / "unpinned" / "oracle"
    shutil.rmtree(oracle)
    write_toy_bundle(oracle, [*STEPS, "checkpoint_3"])

    monkeypatch.setenv(ROOT_ENV, str(problems))
    refused = _analyze(job_dir, store, lock_path, tmp_path / "refused")
    assert refused.exit_code != 0
    assert "cannot be assessed" in refused.output
    assert "does not match the digest recorded" in refused.output


def _write_gate_record(agent_dir: Path, *, clean: bool) -> None:
    """A host-written ``gate/1`` record: one refused seal, one fix turn, then the step's verdict."""

    def seal(run: int, after: str, *, passed: bool) -> dict[str, Any]:
        check = {
            "name": "lint.complexity",
            "category": "lint",
            "blocking": True,
            "status": "passed" if passed else "failed",
            "seconds": 0.5,
            "cause": None if passed else "C901 too complex",
        }
        return {
            "who": "seal",
            "run": run,
            "after": after,
            "verdict": "passed" if passed else "refused",
            "attribution": "passed" if passed else "refused",
            "seconds": 1.0,
            "error": None,
            "checks": [check],
        }

    fix_turn = {
        "stage": "fix",
        "kind": "gate_fix",
        "turn": 1,
        "seconds": 5.0,
        "tokens": {"input": 10, "cached": 0, "output": 1, "cost_usd": 0.001},
    }
    record = {
        "gate/1": {
            "max_fix_rounds": 1,
            "errors": [],
            "pre_fix_snapshots": [],
            "gates": [seal(1, "stage_1_work", passed=False), seal(2, "stage_2_fix", passed=clean)],
        },
        "turns": [fix_turn],
    }
    workflow = agent_dir / "workflow"
    workflow.mkdir(parents=True, exist_ok=True)
    (workflow / "workflow.json").write_text(json.dumps(record))


def test_verification_effort_and_its_default_gate_check_reach_the_report(tmp_path: Path) -> None:
    scorers = ["correctness@10", "verification-effort@1"]
    lock = _lock(scorers)
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    job_dir = _write_run(tmp_path, lock)
    for attempt in range(5):
        for step in STEPS:
            agent_dir = job_dir / "trials" / f"toy-guided-b{attempt}" / "steps" / step / "agent"
            _write_gate_record(agent_dir, clean=True)
    store = tmp_path / "store"
    rescore_path(
        job_dir / "trials",
        tuple(scorers),
        AssessmentStore(store),
        default_scorer_registry(),
        config=lock_rescore_config(lock).scorers,
    )

    output = tmp_path / "analysis"
    result = _analyze(job_dir, store, lock_path, output)

    assert result.exit_code == 0, result.output
    report = json.loads((output / "analysis.json").read_text())
    baseline, guided = report["arms"]
    rate = "gate.first_pass_clean_rate"
    assert baseline["verification_effort"][rate]["gaps"] == {"not_applicable": 5}
    assert guided["verification_effort"][rate]["mean"] == 0.0
    assert guided["verification_effort"]["gate.mean_rounds_to_clean"]["mean"] == 1.0
    assert guided["verification_effort"]["gate.total_first_run_blocking"]["mean"] == 2.0
    (comparison,) = report["comparisons"]
    assert comparison["gate_effects"] == []
    assert "baseline arm measured no verification-effort value" in comparison["gate_note"]
    manipulation = comparison["manipulation"]
    assert manipulation["verdict"] == "insufficient_data"
    (check,) = manipulation["checks"]
    assert check["check"] == "gate_clean_at_step_end"
    assert (check["guided"]["trials"], check["baseline"]["trials"]) == (5, 0)


@pytest.mark.parametrize("damage", ["edit", "add", "remove-manifest", "edit-record"])
def test_tampering_with_a_sealed_trial_refuses_pooling_even_with_every_override(
    tmp_path: Path, damage: str
) -> None:
    path = tmp_path / "solo"
    trial = path / "trials" / "t1"
    trial.mkdir(parents=True)
    (path / "trial-records").mkdir()
    (trial / "result.json").write_text("{}")
    record = path / "trial-records" / "t1.json"
    record.write_text("{}")
    manifest = {
        "job_id": "solo",
        "mechanics_epoch": "mechanics-v1",
        "records_manifests": {"t1": seal_trial(trial, record, path)},
    }
    job = JobDir(path, manifest, {}, ())
    if damage == "edit":
        (trial / "result.json").write_text('{"reward": 1}')
    elif damage == "add":
        (trial / "extra.txt").write_text("x")
    elif damage == "remove-manifest":
        (trial / RECORDS_MANIFEST).unlink()
    else:
        record.write_text('{"valid": true}')

    with pytest.raises(PoolingError, match="never overridable"):
        check_pooling([job], ["mechanics_epoch", "toolchain", "timing_cost"])


def test_setting_the_primary_relocks_the_analysis_section_only(tmp_path: Path) -> None:
    resolvers = ResolverSet([*fixture_resolvers(), MetricResolver()], entry_point_group=None)

    def locked(document: dict[str, Any], name: str) -> dict[str, Any]:
        path = write_experiment(tmp_path / name, document)
        loaded = load_experiment(path)
        lock_path = path.with_name("experiment.lock.json")
        return json.loads(render_lock(build_lock(loaded, lock_path=lock_path, resolvers=resolvers)))

    base = locked(experiment_document(), "base")
    document = experiment_document()
    document["analysis"]["primary_outcome"] = SOLVE_RATE_ERRATA
    primary = locked(document, "primary")

    assert primary["lock_sha256"] == base["lock_sha256"]
    assert "analysis" in stale_sections(base, primary)
    assert "execution" not in stale_sections(base, primary)
    assert primary["analysis"]["primary_outcome"] == SOLVE_RATE_ERRATA
    assert "primary_outcome" not in base["analysis"]
