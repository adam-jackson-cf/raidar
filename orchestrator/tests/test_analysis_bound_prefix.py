"""A ``checkpoints: k`` binding is a k-checkpoint task for scoring and analysis.

The trials' recorded plan (``TrialRecord.planned_steps``) is the authority, not the problem
bundle: the bundle plans every checkpoint, the binding selects the first k. ``MAX_CHECKPOINTS``
is the contrasting smoke truncation: steps past it stay planned and ``not_attempted``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from raidar.contract.trial import CheckpointState, StepResult, TrialRecord
from raidar.design.experiment.lock import seal_lock
from raidar.engine.job.assessment import lock_rescore_config
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.framework.rescore import rescore_path
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.framework.store import AssessmentStore
from tests.scorer_support import APP_V1, APP_V2, StepFixture, write_trial
from tests.test_analysis_command import (
    FAIL,
    PASS,
    _analyze,
    _effect,
    _lock,
    _record,
)

PROBLEM = "scb/toy@1"
ALL_STEPS = ("checkpoint_1", "checkpoint_2", "checkpoint_3")
FILES = (APP_V1, APP_V2, APP_V2)
ATTEMPTS = 5
# The baseline solves every executed checkpoint; the guided arm fails the second.
VERDICTS = {"baseline": (PASS, PASS, PASS), "guided": (PASS, FAIL, PASS)}


def _solvable_lock(bound: int) -> dict[str, Any]:
    """A lock pinning a one-case solvable set for each of the problem's three checkpoints."""
    lock = _lock(["correctness@5"])
    lock["spec"]["problems"] = [{"ref": PROBLEM, "checkpoints": bound}]
    steps = {step: [f"{step}::case_0"] for step in ALL_STEPS}
    lock["execution"]["references"] = {
        "problem": {PROBLEM: {"metadata": {"solvable": {"sha256": "ab" * 32, "steps": steps}}}}
    }
    return seal_lock(
        {
            key: value
            for key, value in lock.items()
            if key not in {"sections", "lock_sha256", "document_sha256"}
        }
    )


def _step_results(record: TrialRecord, count: int) -> tuple[StepResult, ...]:
    return tuple(
        StepResult(
            trial_id=record.trial.trial_id,
            arm=record.trial.arm,
            attempt=record.trial.attempt,
            problem=PROBLEM,
            index=index,
            step=step,
            checkpoint=step,
            checkpoint_state=CheckpointState.SOLVED
            if index < count
            else CheckpointState.NOT_ATTEMPTED,
        )
        for index, step in enumerate(record.trial.steps)
    )


def _write_job(
    root: Path, lock: dict[str, Any], *, bound: int, max_checkpoints: int | None = None
) -> Path:
    """A job whose trials ran ``min(bound, max_checkpoints)`` of the bound prefix's steps."""
    job_dir = root / "run-1"
    executed = bound if max_checkpoints is None else min(bound, max_checkpoints)
    trials = []
    for arm, verdicts in VERDICTS.items():
        for attempt in range(ATTEMPTS):
            trial_id = f"toy-{arm}-b{attempt}"
            fixtures = [
                StepFixture(ALL_STEPS[index], FILES[index], (verdicts[index],))
                for index in range(executed)
            ]
            write_trial(
                job_dir / "trials" / trial_id,
                fixtures,
                planned=ALL_STEPS[executed:],
                problem=PROBLEM,
            )
            record = _record(trial_id, arm, attempt, truncated_to=max_checkpoints)
            planned = ALL_STEPS[:bound]
            record = record.model_copy(
                update={
                    "trial": record.trial.model_copy(update={"steps": planned}),
                    "reached": (True,) * executed + (False,) * (bound - executed),
                }
            )
            if max_checkpoints is not None:
                record = record.model_copy(update={"step_results": _step_results(record, executed)})
            path = job_dir / "trial-records" / f"{trial_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(record.model_dump_json())
            trials.append({**record.trial.model_dump(mode="json"), "selected": True})
    options = {"max_checkpoints": max_checkpoints, "agent_override": None}
    (job_dir / "plan.json").write_text(
        json.dumps({"kind": "raidar-plan", "options": options, "trials": trials})
    )
    manifest = {"kind": "raidar-job", "job_id": "run-1", "lock_sha256": lock["lock_sha256"]}
    (job_dir / "job.json").write_text(json.dumps(manifest))
    (job_dir / "assessment-config.yaml").write_text("scorers: {}\n")
    return job_dir


def _assess_and_analyze(
    tmp_path: Path, bound: int, max_checkpoints: int | None = None
) -> dict[str, Any]:
    lock = _solvable_lock(bound)
    lock_path = tmp_path / "bound.lock.json"
    lock_path.write_text(json.dumps(lock))
    job_dir = _write_job(tmp_path, lock, bound=bound, max_checkpoints=max_checkpoints)
    store = tmp_path / "store"
    records = rescore_path(
        job_dir / "trials",
        ("correctness@5",),
        AssessmentStore(store),
        default_scorer_registry(),
        config=lock_rescore_config(lock).scorers,
    )
    for record in records:
        assessment = AssessmentStore(store).get(record.key)
        assert assessment is not None
        assert [step["step"] for step in assessment.inputs["steps"]] == list(ALL_STEPS[:bound])
        if max_checkpoints is None:
            solved = next(
                item
                for item in assessment.result.observations
                if item.metric == "solved_checkpoints_solvable"
            )
            assert solved.status is AssessStatus.OK
        else:
            assert assessment.inputs["steps"][-1]["reached"] is False
    output = tmp_path / "out"
    extra = ("--allow-dev-jobs",) if max_checkpoints is not None else ()
    result = _analyze(job_dir, store, lock_path, output, *extra)
    assert result.exit_code == 0, result.output
    return json.loads((output / "analysis.json").read_text())


@pytest.mark.parametrize("bound", [1, 2])
def test_a_k_bound_trial_is_a_k_checkpoint_task_for_the_solvable_outcomes(
    tmp_path: Path, bound: int
) -> None:
    report = _assess_and_analyze(tmp_path, bound)

    # Only the bound prefix's solvable sets are reported; the checkpoints past k are absent.
    cases = report["solvable"][PROBLEM]["cases"]
    assert list(cases) == list(ALL_STEPS[:bound])

    # The solvable outcome is available and its denominator is k, not the bundle's three.
    effect = _effect(report, "solve_rate_solvable")
    assert effect["verdict"] != "unavailable"
    assert effect["baseline_mean"] == pytest.approx(1.0)
    guided_solved = sum(1 for verdict in VERDICTS["guided"][:bound] if verdict == PASS)
    assert effect["treatment_mean"] == pytest.approx(guided_solved / bound)


def test_max_checkpoints_keeps_every_bound_step_planned(tmp_path: Path) -> None:
    # The smoke truncation is not the binding: steps past it stay planned, so the summary
    # keeps all three checkpoints' solvable sets and the truncated trial is not scored.
    report = _assess_and_analyze(tmp_path, bound=3, max_checkpoints=2)

    assert list(report["solvable"][PROBLEM]["cases"]) == list(ALL_STEPS)
    assert report["dev_job"]["max_checkpoints"] == 2
    effect = _effect(report, "solve_rate_solvable")
    assert effect["exclusions"]["ineligible_statuses"] == {"truncated": ATTEMPTS}
