"""``raidar analyze`` over stored runs and a real Assessment store (WS-J J1, J2).

Trials are written with host-side oracle records, rescored with the real
``correctness@1`` and ``resource-usage@1`` scorers, and analysed through the
CLI. The analysis must find each trial's Assessments by recomputing the keys
``raidar assess --rescore`` wrote, and must leave runs and the store untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.analysis.sources import AnalysisInputError, RunDir, select_records
from raidar.cli import main
from raidar.execution.models import (
    ExecutionValidity,
    PlannedTrial,
    ProgressionRule,
    TrajectoryRecord,
    TrialHashes,
    ValidityCheck,
)
from raidar.oracle.models import CaseStatus
from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.rescore import rescore_path
from raidar.scorers.store import AssessmentStore
from raidar.spec.canonical import digest_json
from tests.scorer_support import APP_V1, APP_V2, StepFixture, tree_state, write_trial

PASS, FAIL = CaseStatus.PASSED, CaseStatus.FAILED
STEPS = ("checkpoint_1", "checkpoint_2")
HASHES = TrialHashes(
    lock="l", problem="p", arm="a", condition="c", agent="g", protocol="r", trial="t"
)
# Guided solves both checkpoints except in block 3; baseline always fails checkpoint 2.
SOLVES = {
    "baseline": [(PASS, FAIL)] * 5,
    "guided": [(PASS, PASS)] * 3 + [(PASS, FAIL), (PASS, PASS)],
}
COST = {"baseline": 0.01, "guided": 0.03}


def _lock(scorers: list[str]) -> dict[str, Any]:
    body = {
        "experiment": {"id": "pilot"},
        "spec": {
            "analysis": {"baseline_arm": "baseline", "blocks": 5, "seed": 11},
            "assessment": {"scorers": scorers},
            "protocol": {"progression": "continue"},
        },
        "headline_policy": {
            "ref": "scb-canonical@1",
            "selection": "default_by_k",
            "checkpoints": [2],
        },
    }
    return {**body, "lock_sha256": digest_json(body)}


def _record(
    trial_id: str, arm: str, block: int, *, valid: bool = True, truncated_to: int | None = None
) -> TrajectoryRecord:
    return TrajectoryRecord(
        experiment="pilot",
        run_id="run-1",
        trial=PlannedTrial(
            trial_id=trial_id,
            block=block,
            order=0,
            sequence=block,
            arm=arm,
            problem="toy@1",
            problem_index=0,
            steps=STEPS,
            hashes=HASHES,
        ),
        trial_dir=f"trials/{trial_id}",
        progression=ProgressionRule(mode="continue", reward_key="solved"),
        truncated_to=truncated_to,
        attempts=(),
        reached=(True, True),
        reached_source="derived",
        validity=ExecutionValidity(checks=(ValidityCheck(name="steps_started", passed=valid),)),
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
) -> Path:
    run_dir = root / name
    trials = []
    for arm, solves in SOLVES.items():
        for block, (first, second) in enumerate(solves):
            trial_id = f"toy-{arm}-b{block}"
            trial_dir = write_trial(
                run_dir / "trials" / trial_id,
                [StepFixture(STEPS[0], APP_V1, (first,)), StepFixture(STEPS[1], APP_V2, (second,))],
            )
            _add_cost(trial_dir, COST[arm])
            truncated = max_checkpoints if record_truncation else None
            record = _record(trial_id, arm, block, truncated_to=truncated)
            path = run_dir / "trajectories" / f"{trial_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(record.model_dump_json())
            trials.append({**record.trial.model_dump(mode="json"), "selected": True})
    options = {"max_checkpoints": max_checkpoints, "agent_override": None}
    plan = {"kind": "raidar-plan", "options": options, "trials": trials}
    (run_dir / "plan.json").write_text(json.dumps(plan))
    manifest = {"kind": "raidar-run", "run_id": name, "lock_sha256": lock["lock_sha256"]}
    (run_dir / "run.json").write_text(json.dumps(manifest))
    (run_dir / "assessment-config.yaml").write_text("scorers: {}\n")
    return run_dir


@pytest.fixture
def pilot(tmp_path: Path) -> tuple[Path, Path, Path]:
    lock = _lock(["correctness@1", "code-quality@1", "resource-usage@1"])
    lock_path = tmp_path / "pilot.lock.json"
    lock_path.write_text(json.dumps(lock))
    run_dir = _write_run(tmp_path, lock)
    store = tmp_path / "store"
    rescore_path(
        run_dir / "trials",
        ("correctness@1", "resource-usage@1"),
        AssessmentStore(store),
        default_scorer_registry(),
    )
    return run_dir, store, lock_path


def _analyze(run_dir: Path | list[Path], store: Path, lock_path: Path, output: Path, *extra: str):
    run_dirs = run_dir if isinstance(run_dir, list) else [run_dir]
    return CliRunner().invoke(
        main,
        [
            "analyze",
            *map(str, run_dirs),
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
    run_dir, store, lock_path = pilot
    before = (tree_state(run_dir), tree_state(store))
    output = tmp_path / "analysis"
    result = _analyze(run_dir, store, lock_path, output, "--min-effect", "solve_rate=0.25")
    assert result.exit_code == 0, result.output
    assert (tree_state(run_dir), tree_state(store)) == before
    report = json.loads((output / "analysis.json").read_text())
    assert report["baseline_arm"] == "baseline"
    assert report["settings"]["seed"] == 11
    solve = _effect(report, "solve_rate")
    assert (solve["n_pairs"], solve["verdict"]) == (5, "improved")
    assert solve["mean_delta"] == pytest.approx(0.4)
    assert solve["cost"]["delta_mean"] == pytest.approx(0.04)  # two steps x 0.02 USD
    assert solve["sample_size"]["delta_source"] == "configured"
    assert solve["sample_size"]["recommended_pairs"] == 7  # ceil(7.84 * (0.2236 / 0.25)^2)
    # The primary outcome of a continue experiment is C; S is secondary. code-quality@1
    # was never rescored, so S is unassessed (never zero) while C still gets its verdict.
    assert report["primary_metric"] == "solve_rate"
    assert report["comparisons"][0]["primary"]["metric"] == "solve_rate"
    assert "primary solve_rate improved" in result.output
    composite = _effect(report, "headline_score")
    assert composite["exclusions"]["unassessed"] == 5
    assert composite["verdict"] == "insufficient"
    # Thresholds are not pinned by the lock yet: say so, and name every non-default.
    settings = report["settings"]
    assert (settings["source"], settings["pinned_by_lock"]) == ("command line", False)
    assert settings["changed_from_defaults"] == ["bootstrap_samples", "min_effect.solve_rate"]
    assert "verdict settings changed from defaults" in result.output
    assert report["dev_run"] is None
    final = _effect(report, "final_solved")
    assert final["mcnemar"]["treatment_only"] == 4
    assert final["verdict"] == "inconclusive"
    markdown = (output / "analysis.md").read_text()
    assert "## guided vs baseline" in markdown
    assert "**Primary outcome** `solve_rate`" in markdown
    assert "not pinned by the lock" in markdown


def test_an_empty_store_leaves_every_pair_unassessed(
    pilot: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    run_dir, _, lock_path = pilot
    empty = tmp_path / "empty-store"
    empty.mkdir()
    assert _analyze(run_dir, empty, lock_path, tmp_path / "out").exit_code == 0
    report = json.loads((tmp_path / "out" / "analysis.json").read_text())
    solve = _effect(report, "solve_rate")
    assert solve["exclusions"]["unassessed"] == 5
    assert solve["verdict"] == "insufficient"


def test_analyze_refuses_to_write_into_a_run_or_to_mix_locks(
    pilot: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    run_dir, store, lock_path = pilot
    inside = _analyze(run_dir, store, lock_path, run_dir / "analysis")
    assert inside.exit_code != 0
    assert "outside" in inside.output
    other = tmp_path / "other.lock.json"
    other.write_text(json.dumps(_lock(["correctness@1"])))
    mixed = _analyze(run_dir, store, other, tmp_path / "out")
    assert mixed.exit_code != 0
    assert "executed under lock" in mixed.output
    tampered = json.loads(lock_path.read_text())
    tampered["spec"]["analysis"]["baseline_arm"] = "guided"
    lock_path.write_text(json.dumps(tampered))
    refused = _analyze(run_dir, store, lock_path, tmp_path / "out")
    assert refused.exit_code != 0
    assert "does not match" in refused.output


def test_a_valid_rerun_replaces_an_invalid_trajectory_but_two_valid_ones_conflict() -> None:
    first = RunDir(Path("run-1"), {"run_id": "run-1"}, {}, (_record("t", "a", 0, valid=False),))
    rerun = RunDir(Path("run-2"), {"run_id": "run-2"}, {}, (_record("t", "a", 0),))
    chosen, replaced = select_records([first, rerun])
    assert chosen["t"][0] is rerun
    assert replaced == ("run-1/t",)
    chosen, replaced = select_records([rerun, first])
    assert chosen["t"][0] is rerun
    assert replaced == ("run-1/t",)
    with pytest.raises(AnalysisInputError, match="valid in both"):
        select_records([rerun, rerun])


def test_truncated_runs_are_refused_unless_labelled_and_never_pooled(tmp_path: Path) -> None:
    lock = _lock(["correctness@1", "resource-usage@1"])
    lock_path = tmp_path / "pilot.lock.json"
    lock_path.write_text(json.dumps(lock))
    truncated = _write_run(tmp_path, lock, name="dev", max_checkpoints=2)
    store = tmp_path / "store"
    rescore_path(
        truncated / "trials",
        ("correctness@1", "resource-usage@1"),
        AssessmentStore(store),
        default_scorer_registry(),
    )
    refused = _analyze(truncated, store, lock_path, tmp_path / "out")
    assert refused.exit_code != 0
    assert "not arm evidence" in refused.output
    assert "--allow-dev-runs" in refused.output

    output = tmp_path / "dev-analysis"
    labelled = _analyze(truncated, store, lock_path, output, "--allow-dev-runs")
    assert labelled.exit_code == 0, labelled.output
    report = json.loads((output / "analysis.json").read_text())
    assert report["dev_run"]["max_checkpoints"] == 2
    # A truncated trajectory has no final checkpoint or C over the problem's K.
    for metric in ("solve_rate", "final_solved", "headline_score"):
        effect = _effect(report, metric)
        assert effect["exclusions"]["ineligible_statuses"] == {"truncated": 5}
        assert effect["verdict"] == "insufficient"
    assert "Development run, not arm evidence" in (output / "analysis.md").read_text()

    full = _write_run(tmp_path, lock, name="full")
    mixed = _analyze([truncated, full], store, lock_path, tmp_path / "mixed", "--allow-dev-runs")
    assert mixed.exit_code != 0
    assert "different development options" in mixed.output
    unrecorded = _write_run(tmp_path, lock, name="odd", max_checkpoints=2, record_truncation=False)
    disagree = _analyze(unrecorded, store, lock_path, tmp_path / "odd-out", "--allow-dev-runs")
    assert disagree.exit_code != 0
    assert "but the plan declares" in disagree.output
