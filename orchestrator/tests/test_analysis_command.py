"""``raidar analyze`` over stored jobs and a real Assessment store.

Trials are written with host-side verifier records, rescored with the real
``correctness@1`` and ``resource-usage@1`` scorers, and analysed through the
CLI. The analysis must find each trial's Assessments by recomputing the keys
``raidar assess --rescore`` wrote, and must leave jobs and the store untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.contract.trial import (
    ExecutionValidity,
    PlannedTrial,
    ProgressionRule,
    TrialHashes,
    TrialRecord,
    ValidityCheck,
)
from raidar.design.experiment.lock import LOCK_VERSION, seal_lock
from raidar.engine.job.assessment import lock_rescore_config
from raidar.evaluation.analysis.sources import (
    AnalysisInputError,
    JobDir,
    default_lock,
    select_records,
)
from raidar.grading.models import CaseStatus
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.framework.rescore import rescore_path
from raidar.scoring.framework.store import AssessmentStore
from tests.scorer_support import APP_V1, APP_V2, StepFixture, tree_state, write_trial

PASS, FAIL = CaseStatus.PASSED, CaseStatus.FAILED
STEPS = ("checkpoint_1", "checkpoint_2")
HASHES = TrialHashes(
    lock="l", problem="p", arm="a", condition="c", agent="g", protocol="r", trial="t"
)
# Guided solves both checkpoints except in attempt 3; baseline always fails checkpoint 2.
SOLVES = {
    "baseline": [(PASS, FAIL)] * 5,
    "guided": [(PASS, PASS)] * 3 + [(PASS, FAIL), (PASS, PASS)],
}
COST = {"baseline": 0.01, "guided": 0.03}


def _lock(
    scorers: list[str],
    *,
    experiment: str = "pilot",
    headline: str = "scb-canonical@1",
    **thresholds: Any,
) -> dict[str, Any]:
    analysis = {"baseline_arm": "baseline", **thresholds}
    spec = {
        "kind": "ab-test",
        "runtime": "python",
        "analysis": {**analysis, "attempts": 5, "seed": 11},
        "assessment": {"scorers": scorers},
        "protocol": {"progression": "continue"},
    }
    return seal_lock(
        {
            "lock_version": LOCK_VERSION,
            "experiment": {"id": experiment},
            "spec": spec,
            "execution": {"design": {"id": experiment, "attempts": 5, "seed": 11}},
            "assessment": {
                "spec": spec["assessment"],
                "metric": {
                    "ref": headline,
                    "selection": "default_by_k",
                    "checkpoints": [2],
                },
            },
            "analysis": analysis,
            "toolchain": {},
        }
    )


# Analysis looks Assessments up under the lock's assessment settings.
LOCKED_SETTINGS = lock_rescore_config(_lock(["correctness@1"]))


def _record(
    trial_id: str, arm: str, attempt: int, *, valid: bool = True, truncated_to: int | None = None
) -> TrialRecord:
    return TrialRecord(
        experiment="pilot",
        job_id="run-1",
        trial=PlannedTrial(
            trial_id=trial_id,
            attempt=attempt,
            order=0,
            sequence=attempt,
            arm=arm,
            problem="toy@1",
            problem_index=0,
            steps=STEPS,
            hashes=HASHES,
        ),
        trial_dir=f"trials/{trial_id}",
        progression=ProgressionRule(mode="continue", reward_key="solved"),
        truncated_to=truncated_to,
        step_results=(),
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
    lock = _lock(["correctness@1", "code-quality@3", "resource-usage@1"])
    lock_path = tmp_path / "pilot.lock.json"
    lock_path.write_text(json.dumps(lock))
    job_dir = _write_run(tmp_path, lock)
    store = tmp_path / "store"
    rescore_path(
        job_dir / "trials",
        ("correctness@1", "resource-usage@1"),
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
    result = _analyze(job_dir, store, lock_path, output, "--min-effect", "solve_rate=0.25")
    assert result.exit_code == 0, result.output
    assert (tree_state(job_dir), tree_state(store)) == before
    report = json.loads((output / "analysis.json").read_text())
    assert report["baseline_arm"] == "baseline"
    assert report["settings"]["seed"] == 11
    solve = _effect(report, "solve_rate")
    assert (solve["n_pairs"], solve["verdict"]) == (5, "improved")
    assert solve["mean_delta"] == pytest.approx(0.4)
    assert solve["cost"]["delta_mean"] == pytest.approx(0.04)  # two steps x 0.02 USD
    assert solve["sample_size"]["delta_source"] == "configured"
    assert solve["sample_size"]["recommended_pairs"] == 7  # ceil(7.84 * (0.2236 / 0.25)^2)
    # The primary outcome of a continue experiment is C; S is secondary. code-quality@3
    # was never rescored, so S is unassessed (never zero) while C still gets its verdict.
    assert report["primary_metric"] == "solve_rate"
    assert report["comparisons"][0]["primary"]["metric"] == "solve_rate"
    assert "primary solve_rate improved" in result.output
    composite = _effect(report, "headline_score")
    assert composite["exclusions"]["unassessed"] == 5
    assert composite["verdict"] == "insufficient"
    # This lock predates pinned thresholds: say so, and name every override and non-default.
    settings = report["settings"]
    assert settings["pinned_by_lock"] is False
    assert settings["source"] == (
        "defaults (lock does not pin thresholds); command line overrides min_effect.solve_rate"
    )
    assert settings["overridden"] == ["min_effect.solve_rate"]
    assert settings["changed_from_defaults"] == ["bootstrap_samples", "min_effect.solve_rate"]
    assert "the lock does not pin verdict thresholds" in result.output
    assert "verdict settings changed from defaults" in result.output
    assert report["dev_job"] is None
    final = _effect(report, "final_solved")
    assert final["mcnemar"]["treatment_only"] == 4
    assert final["verdict"] == "inconclusive"
    markdown = (output / "analysis.md").read_text()
    assert "## guided vs baseline" in markdown
    assert "**Primary outcome** `solve_rate`" in markdown
    assert "not pinned by the lock" in markdown


def test_floor_policy_scores_c_zero_trials_zero_end_to_end_while_the_base_policy_leaves_them_unscored(
    tmp_path: Path,
) -> None:
    # S2-SS-2 through rescore and `raidar analyze`: code-quality@3 is never rescored, so every
    # trial has a quality gap. Under @2 a trial with C = 0 still scores S = 0 and is
    # paired; one with C > 0 stays unassessed. Under @1 (fork-exact) every S is unassessed.
    outcomes = {
        "baseline": [(FAIL, FAIL)] * 5,
        "guided": [(FAIL, FAIL)] * 2 + [(PASS, FAIL)] * 3,
    }
    scorers = ["correctness@1", "code-quality@3", "resource-usage@1"]
    for headline, pairs, unassessed in (("scb-canonical@2", 2, 3), ("scb-canonical@1", 0, 5)):
        root = tmp_path / headline
        lock = _lock(scorers, headline=headline)
        lock_path = root / "pilot.lock.json"
        root.mkdir()
        lock_path.write_text(json.dumps(lock))
        job_dir = _write_run(root, lock, outcomes=outcomes)
        store = root / "store"
        rescore_path(
            job_dir / "trials",
            ("correctness@1", "resource-usage@1"),
            AssessmentStore(store),
            default_scorer_registry(),
            config=lock_rescore_config(lock).scorers,
        )
        result = _analyze(job_dir, store, lock_path, root / "analysis")
        assert result.exit_code == 0, result.output
        report = json.loads((root / "analysis" / "analysis.json").read_text())
        correctness = _effect(report, "correctness")
        assert correctness["n_pairs"] == 5
        assert correctness["mean_delta"] == pytest.approx(0.3)  # 3 of 5 guided at C = 0.5
        headline_effect = _effect(report, "headline_score")
        assert headline_effect["n_pairs"] == pairs, headline
        assert headline_effect["exclusions"]["unassessed"] == unassessed, headline
        if pairs:
            assert headline_effect["mean_delta"] == 0.0  # both arms S = 0 on the C = 0 pairs


PINNED = {
    "min_pairs": 6,
    "max_exclusion_fraction": 0.2,
    "alpha": 0.05,
    "min_effects": {"solve_rate": 0.25},
}


def _pinned_pilot(tmp_path: Path) -> tuple[Path, Path, Path]:
    lock = _lock(["correctness@1", "resource-usage@1"], **PINNED)
    lock_path = tmp_path / "pinned.lock.json"
    lock_path.write_text(json.dumps(lock))
    job_dir = _write_run(tmp_path / "pinned", lock)
    store = tmp_path / "pinned-store"
    rescore_path(
        job_dir / "trials",
        ("correctness@1", "resource-usage@1"),
        AssessmentStore(store),
        default_scorer_registry(),
        config=LOCKED_SETTINGS.scorers,
    )
    return job_dir, store, lock_path


def test_the_lock_pins_verdict_thresholds_and_overrides_are_labelled(tmp_path: Path) -> None:
    job_dir, store, lock_path = _pinned_pilot(tmp_path)
    pinned = _analyze(job_dir, store, lock_path, tmp_path / "pinned-out")
    assert pinned.exit_code == 0, pinned.output
    report = json.loads((tmp_path / "pinned-out" / "analysis.json").read_text())
    settings = report["settings"]
    assert (settings["source"], settings["pinned_by_lock"]) == ("lock", True)
    assert (settings["min_pairs"], settings["min_effects"]) == (6, {"solve_rate": 0.25})
    assert settings["overridden"] == []
    solve = _effect(report, "solve_rate")
    assert solve["verdict"] == "insufficient"
    assert solve["verdict_reason"] == "5 pairs < minimum 6 (pinned by the lock)"
    assert solve["sample_size"]["delta_source"] == "configured"
    assert "pinned by the lock; overridden on the command line: none" in (
        (tmp_path / "pinned-out" / "analysis.md").read_text()
    )

    overridden = _analyze(job_dir, store, lock_path, tmp_path / "override", "--min-pairs", "5")
    assert overridden.exit_code == 0, overridden.output
    assert "lock-pinned thresholds overridden: min_pairs" in overridden.output
    report = json.loads((tmp_path / "override" / "analysis.json").read_text())
    assert report["settings"]["source"] == "lock; command line overrides min_pairs"
    assert report["settings"]["overridden"] == ["min_pairs"]
    assert _effect(report, "solve_rate")["verdict"] == "improved"
    assert _effect(report, "headline_score")["verdict_reason"].endswith(
        "(command-line override of the lock)"
    )


def test_an_empty_store_leaves_every_pair_unassessed(
    pilot: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    job_dir, _, lock_path = pilot
    empty = tmp_path / "empty-store"
    empty.mkdir()
    assert _analyze(job_dir, empty, lock_path, tmp_path / "out").exit_code == 0
    report = json.loads((tmp_path / "out" / "analysis.json").read_text())
    solve = _effect(report, "solve_rate")
    assert solve["exclusions"]["unassessed"] == 5
    assert solve["verdict"] == "insufficient"


def test_analyze_refuses_to_write_into_a_run_or_to_mix_locks(
    pilot: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    job_dir, store, lock_path = pilot
    inside = _analyze(job_dir, store, lock_path, job_dir / "analysis")
    assert inside.exit_code != 0
    assert "outside" in inside.output
    other = tmp_path / "other.lock.json"
    other.write_text(json.dumps(_lock(["correctness@1"], experiment="other")))
    mixed = _analyze(job_dir, store, other, tmp_path / "out")
    assert mixed.exit_code != 0
    assert "executed under lock" in mixed.output
    # A relock that only changes the scorers keeps the execution identity the run recorded.
    rescored = _lock(["correctness@1"])
    assert rescored["lock_sha256"] == json.loads(lock_path.read_text())["lock_sha256"]
    other.write_text(json.dumps(rescored))
    assert _analyze(job_dir, store, other, tmp_path / "rescored").exit_code == 0
    tampered = json.loads(lock_path.read_text())
    tampered["spec"]["analysis"]["baseline_arm"] = "guided"
    lock_path.write_text(json.dumps(tampered))
    refused = _analyze(job_dir, store, lock_path, tmp_path / "out")
    assert refused.exit_code != 0
    assert "does not match" in refused.output


def test_a_valid_rerun_replaces_an_invalid_trial_but_two_valid_ones_conflict() -> None:
    first = JobDir(Path("run-1"), {"job_id": "run-1"}, {}, (_record("t", "a", 0, valid=False),))
    rerun = JobDir(Path("run-2"), {"job_id": "run-2"}, {}, (_record("t", "a", 0),))
    chosen, replaced = select_records([first, rerun])
    assert chosen["t"][0] is rerun
    assert replaced == ("run-1/t",)
    chosen, replaced = select_records([rerun, first])
    assert chosen["t"][0] is rerun
    assert replaced == ("run-1/t",)
    with pytest.raises(AnalysisInputError, match="valid in both"):
        select_records([rerun, rerun])


def test_a_job_resolves_only_the_lock_its_manifest_names_and_otherwise_asks_for_one(
    tmp_path: Path,
) -> None:
    lock = _lock(["correctness@1"])
    experiment_file = tmp_path / "pilot.yaml"
    lock_path = tmp_path / "pilot.lock.json"
    lock_path.write_text(json.dumps(lock))
    manifest = {"job_id": "j", "lock_sha256": lock["lock_sha256"]}
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "job.lock.json").write_text(json.dumps(lock))  # never a fallback

    def job(**extra: object) -> JobDir:
        return JobDir(job_dir, {**manifest, **extra}, {}, ())

    assert default_lock(job(experiment_file=str(experiment_file))) == lock_path
    with pytest.raises(AnalysisInputError, match="names no experiment_file; pass --lock"):
        default_lock(job())
    lock_path.write_text(json.dumps(_lock(["correctness@1"], experiment="another")))
    with pytest.raises(AnalysisInputError, match="another experiment.*pass --lock"):
        default_lock(job(experiment_file=str(experiment_file)))
    lock_path.unlink()
    with pytest.raises(AnalysisInputError, match="pass --lock"):
        default_lock(job(experiment_file=str(experiment_file)))


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
        config=LOCKED_SETTINGS.scorers,
    )
    refused = _analyze(truncated, store, lock_path, tmp_path / "out")
    assert refused.exit_code != 0
    assert "not arm evidence" in refused.output
    assert "--allow-dev-jobs" in refused.output

    output = tmp_path / "dev-analysis"
    labelled = _analyze(truncated, store, lock_path, output, "--allow-dev-jobs")
    assert labelled.exit_code == 0, labelled.output
    report = json.loads((output / "analysis.json").read_text())
    assert report["dev_job"]["max_checkpoints"] == 2
    # A truncated trial has no final checkpoint or C over the problem's K.
    for metric in ("solve_rate", "final_solved", "headline_score"):
        effect = _effect(report, metric)
        assert effect["exclusions"]["ineligible_statuses"] == {"truncated": 5}
        assert effect["verdict"] == "insufficient"
    assert "Development job, not arm evidence" in (output / "analysis.md").read_text()

    full = _write_run(tmp_path, lock, name="full")
    mixed = _analyze([truncated, full], store, lock_path, tmp_path / "mixed", "--allow-dev-jobs")
    assert mixed.exit_code != 0
    assert "different development options" in mixed.output
    unrecorded = _write_run(tmp_path, lock, name="odd", max_checkpoints=2, record_truncation=False)
    disagree = _analyze(unrecorded, store, lock_path, tmp_path / "odd-out", "--allow-dev-jobs")
    assert disagree.exit_code != 0
    assert "but the plan declares" in disagree.output


def test_rescore_and_analysis_follow_the_lock_not_the_runs_execution_time_file(
    tmp_path: Path,
) -> None:
    lock = _lock(["correctness@1", "code-quality@3", "resource-usage@1"])
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
            "correctness@1",
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


def test_a_moved_oracle_bundle_is_refused_or_found_by_digest_never_unassessed(
    pilot: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    job_dir, store, lock_path = pilot
    moved = tmp_path / "moved-bundles"
    moved.mkdir()
    for bundle in sorted((job_dir / "trials").glob("*-bundle")):
        bundle.rename(moved / bundle.name)

    refused = _analyze(job_dir, store, lock_path, tmp_path / "refused")
    assert refused.exit_code != 0
    assert "cannot be assessed" in refused.output
    assert "--bundle-search" in refused.output

    found = _analyze(job_dir, store, lock_path, tmp_path / "found", "--bundle-search", str(moved))
    assert found.exit_code == 0, found.output
    report = json.loads((tmp_path / "found" / "analysis.json").read_text())
    assert _effect(report, "solve_rate")["n_pairs"] == 5


@pytest.mark.parametrize("process", ["process-compliance@6", None])
def test_the_manipulation_check_reads_the_declared_process_compliance_version(
    tmp_path: Path, process: str | None
) -> None:
    # Jev parked (2026-10-04): the deterministic scorer is the default; a lock declaring
    # no process-compliance version gets no manipulation check.
    scorers = ["correctness@1", *([process] if process else [])]
    lock = _lock(scorers)
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    job_dir = _write_run(tmp_path, lock)
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
    (comparison,) = json.loads((output / "analysis.json").read_text())["comparisons"]
    if process is None:
        assert comparison["manipulation"] is None
        return
    manipulation = comparison["manipulation"]
    assert manipulation["kind"] == "raidar-manipulation-check"
    # These trials carry no trajectory, so no check is decided in either arm.
    assert manipulation["verdict"] == "insufficient_data"
    assert manipulation["checks"][0]["reason"] == "an arm has no trial with a decided check"


def _write_gate_record(agent_dir: Path, *, clean: bool) -> None:
    """A guardrail-gate@1 record: one refused run, one fix, then the step's verdict."""
    gate = agent_dir / "guardrail-gate"
    gate.mkdir(parents=True, exist_ok=True)
    refused = (
        "guardrail-check: REFUSED (1 blocking, 0 warnings, 1 Python files)\n"
        "Blocking (fix these) (1):\n  app/main.py:1:1: C901 too complex\n"
    )
    passed = "guardrail-check: PASSED (0 blocking, 0 warnings, 1 Python files)\n"
    (gate / "gate_run_1.log").write_text(refused)
    (gate / "gate_run_2.log").write_text(passed if clean else refused)
    runs = [
        {
            "run": 1,
            "after": "stage_1_work",
            "passed": False,
            "log": "guardrail-gate/gate_run_1.log",
        },
        {"run": 2, "after": "stage_2_fix", "passed": clean, "log": "guardrail-gate/gate_run_2.log"},
    ]
    stages = [
        {"index": index, "stage": role, "round": index - 1, "n_input_tokens": 10,
         "n_output_tokens": 1, "cost_usd": 0.001, "duration_sec": 5.0}
        for index, role in ((1, "work"), (2, "fix"))
    ]  # fmt: skip
    record = {
        "termination_reason": "gate_clean" if clean else "rounds_exhausted",
        "rounds_configured": 1,
        "gate_runs": runs,
        "stages": stages,
        "gate_clean_at_step_end": clean,
    }
    (gate / "guardrail-gate.json").write_text(json.dumps(record))


def test_gate_effort_and_the_preregistered_gate_check_reach_the_report(tmp_path: Path) -> None:
    scorers = ["correctness@1", "gate-effort@10"]
    gate_check = [{"check": "gate_clean_at_step_end", "min_share": 0.8}]
    lock = _lock(scorers, manipulation_checks=gate_check)
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
    assert baseline["gate_effort"][rate]["gaps"] == {"not_applicable": 5}
    assert guided["gate_effort"][rate]["mean"] == 0.0
    assert guided["gate_effort"]["gate.mean_rounds_to_clean"]["mean"] == 1.0
    assert guided["gate_effort"]["gate.total_first_run_blocking.complexity"]["mean"] == 2.0
    (comparison,) = report["comparisons"]
    assert comparison["gate_effects"] == []
    assert "baseline arm measured no gate-effort value" in comparison["gate_note"]
    manipulation = comparison["manipulation"]
    assert (manipulation["preregistered"], manipulation["verdict"]) == (True, "separated")
    (check,) = manipulation["checks"]
    assert (check["basis"], check["guided_steps"]["decided"]) == ("threshold", 10)
    markdown = (output / "analysis.md").read_text()
    assert "## Gate effort (gate-effort@10, per arm)" in markdown
