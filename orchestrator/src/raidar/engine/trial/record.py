"""Assemble and write one trial's ``TrialRecord`` (plan D2-D4)."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from raidar.contract.trial import (
    ExecutionValidity,
    PhaseSample,
    PlannedTrial,
    ProgressionRule,
    StepResult,
    TrialRecord,
    ValidityCheck,
)
from raidar.engine.perf.phases import trial_phases
from raidar.engine.trial.collect import (
    TrialContext,
    classify_trial,
    execution_validity,
    trial_exception,
)
from raidar.engine.trial.materialise import MaterialisedTask, compose_init
from raidar.engine.trial.progression import derive_reached, strict_rule


@dataclass(frozen=True)
class TrialEvidence:
    """What a finished trial left behind, plus what classification needs to read it."""

    trial: PlannedTrial
    trial_dir: Path
    task: MaterialisedTask
    planned_steps: tuple[tuple[str, str], ...]
    pinned_harness_version: str | None
    environment_cold: bool
    runner_exception: str | None


def reached(
    rule: ProgressionRule, step_results: Sequence[StepResult], max_checkpoints: int | None
) -> tuple[tuple[bool, ...], Literal["executed", "derived"]]:
    """SCB-strict ``reached`` (D3): the strict gate replayed over the step rewards.

    The same replay serves both modes, so a trial whose infrastructure broke
    before any step ran reads the same either way. ``executed`` marks a halt
    run, whose executed prefix the validity gate checks against its own
    ``min_reward`` gate (``collect._halt_check``).
    """
    rewards = [a.rewards for a in step_results]
    vector = derive_reached(rewards, strict_rule(rule), runnable=max_checkpoints)
    return vector, "executed" if rule.mode == "halt_on_failure" else "derived"


def _stored_dir(trial_dir: Path, job_dir: Path) -> str:
    return (
        trial_dir.relative_to(job_dir).as_posix()
        if trial_dir.is_relative_to(job_dir)
        else str(trial_dir)
    )


def build_record(
    evidence: TrialEvidence,
    *,
    experiment: str,
    job_id: str,
    job_dir: Path,
    rule: ProgressionRule,
    max_checkpoints: int | None,
    agent_override: str | None,
) -> TrialRecord:
    context = TrialContext(
        trial=evidence.trial,
        planned_steps=evidence.planned_steps,
        task_dir=evidence.task.path,
        compiled=evidence.task.compiled,
        pinned_harness_version=evidence.pinned_harness_version,
        rule=rule,
    )
    step_results = classify_trial(evidence.trial_dir, context)
    reached_vector, source = reached(rule, step_results, max_checkpoints)
    phases = trial_phases(
        evidence.trial_dir,
        component_apply_s=evidence.task.component_apply_s,
        environment_cold=evidence.environment_cold,
        compose_init=compose_init(evidence.task.path),
    )
    return TrialRecord(
        experiment=experiment,
        job_id=job_id,
        trial=evidence.trial,
        trial_dir=_stored_dir(evidence.trial_dir, job_dir),
        progression=rule,
        truncated_to=max_checkpoints,
        agent_override=agent_override,
        step_results=step_results,
        reached=reached_vector,
        reached_source=source,
        validity=execution_validity(
            evidence.trial_dir,
            step_results,
            evidence.pinned_harness_version,
            rule,
            runnable=max_checkpoints,
        ),
        phases=tuple(phases),
        trial_exception=trial_exception(evidence.trial_dir) or evidence.runner_exception,
    )


RUNNER_EXCEPTION = "runner_exception"
_DETAIL_LIMIT = 500


@dataclass(frozen=True)
class RunnerFailure:
    """A trial whose Raidar side raised: materialising, starting or classifying it."""

    trial: PlannedTrial
    planned_steps: tuple[tuple[str, str], ...]
    trial_dir: Path
    error: BaseException


def _unrun_step(
    trial: PlannedTrial, index: int, step: str, checkpoint: str, reason: str
) -> StepResult:
    identity = {
        "trial_id": trial.trial_id,
        "arm": trial.arm,
        "attempt": trial.attempt,
        "problem": trial.problem,
        "index": index,
        "step": step,
        "checkpoint": checkpoint,
    }
    if step not in trial.steps:
        return StepResult.model_validate(
            {**identity, "checkpoint_state": "not_attempted", "termination": "truncated"}
        )
    return StepResult.model_validate(
        {
            **identity,
            "checkpoint_state": "infra_error",
            "infra_reasons": (reason,),
            "termination": RUNNER_EXCEPTION,
        }
    )


def runner_failure_record(
    failure: RunnerFailure,
    *,
    experiment: str,
    job_id: str,
    job_dir: Path,
    rule: ProgressionRule,
    max_checkpoints: int | None,
    agent_override: str | None,
) -> TrialRecord:
    """The record of a trial that could not run or be read: every planned step is infra.

    Truncated steps stay ``not_attempted``; everything else is ``infra_error``
    with the exception type, and the trial is invalid, so it is excluded
    as infrastructure rather than lost or counted as an agent failure.
    """
    error = type(failure.error).__name__
    reason = f"{RUNNER_EXCEPTION}:{error}"
    step_results = tuple(
        _unrun_step(failure.trial, index, step, checkpoint, reason)
        for index, (step, checkpoint) in enumerate(failure.planned_steps)
    )
    reached_vector, source = reached(rule, step_results, max_checkpoints)
    detail = f"{error}: {failure.error}"[:_DETAIL_LIMIT]
    return TrialRecord(
        experiment=experiment,
        job_id=job_id,
        trial=failure.trial,
        trial_dir=_stored_dir(failure.trial_dir, job_dir),
        progression=rule,
        truncated_to=max_checkpoints,
        agent_override=agent_override,
        step_results=step_results,
        reached=reached_vector,
        reached_source=source,
        validity=ExecutionValidity(
            checks=(ValidityCheck(name=RUNNER_EXCEPTION, passed=False, detail=detail),)
        ),
        trial_exception=error,
    )


def write_record(path: Path, record: TrialRecord) -> TrialRecord:
    """Write the record, timing the write itself as the N1 *raidar_result_write* phase."""
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    path.write_text(record.model_dump_json(indent=2) + "\n")
    elapsed = time.perf_counter() - started
    sample = PhaseSample(phase="raidar_result_write", seconds=elapsed)
    final = record.model_copy(update={"phases": (*record.phases, sample)})
    path.write_text(final.model_dump_json(indent=2) + "\n")
    return final
