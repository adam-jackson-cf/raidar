"""Assemble and write one trial's ``TrajectoryRecord`` (plan D2-D4)."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from raidar.execution.collect import (
    TrialContext,
    classify_trial,
    execution_validity,
    trial_exception,
)
from raidar.execution.materialise import MaterialisedTask, compose_init
from raidar.execution.models import (
    CheckpointAttempt,
    ExecutionValidity,
    PhaseSample,
    PlannedTrial,
    ProgressionRule,
    TrajectoryRecord,
    ValidityCheck,
)
from raidar.execution.phases import trial_phases
from raidar.execution.progression import derive_reached, strict_rule


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
    rule: ProgressionRule, attempts: Sequence[CheckpointAttempt], max_checkpoints: int | None
) -> tuple[tuple[bool, ...], Literal["executed", "derived"]]:
    """SCB-strict ``reached`` (D3): the strict gate replayed over the step rewards.

    The same replay serves both modes, so a trial whose infrastructure broke
    before any step ran reads the same either way. ``executed`` marks a halt
    run, whose executed prefix the validity gate checks against its own
    ``min_reward`` gate (``collect._halt_check``).
    """
    rewards = [a.rewards for a in attempts]
    vector = derive_reached(rewards, strict_rule(rule), runnable=max_checkpoints)
    return vector, "executed" if rule.mode == "halt_on_failure" else "derived"


def _stored_dir(trial_dir: Path, run_dir: Path) -> str:
    return (
        trial_dir.relative_to(run_dir).as_posix()
        if trial_dir.is_relative_to(run_dir)
        else str(trial_dir)
    )


def build_record(
    evidence: TrialEvidence,
    *,
    experiment: str,
    run_id: str,
    run_dir: Path,
    rule: ProgressionRule,
    max_checkpoints: int | None,
    agent_override: str | None,
) -> TrajectoryRecord:
    context = TrialContext(
        trial=evidence.trial,
        planned_steps=evidence.planned_steps,
        task_dir=evidence.task.path,
        compiled=evidence.task.compiled,
        pinned_harness_version=evidence.pinned_harness_version,
        rule=rule,
    )
    attempts = classify_trial(evidence.trial_dir, context)
    reached_vector, source = reached(rule, attempts, max_checkpoints)
    phases = trial_phases(
        evidence.trial_dir,
        component_apply_s=evidence.task.component_apply_s,
        environment_cold=evidence.environment_cold,
        compose_init=compose_init(evidence.task.path),
    )
    return TrajectoryRecord(
        experiment=experiment,
        run_id=run_id,
        trial=evidence.trial,
        trial_dir=_stored_dir(evidence.trial_dir, run_dir),
        progression=rule,
        truncated_to=max_checkpoints,
        agent_override=agent_override,
        attempts=attempts,
        reached=reached_vector,
        reached_source=source,
        validity=execution_validity(
            evidence.trial_dir,
            attempts,
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


def _unrun_attempt(
    trial: PlannedTrial, index: int, step: str, checkpoint: str, reason: str
) -> CheckpointAttempt:
    identity = {
        "trial_id": trial.trial_id,
        "arm": trial.arm,
        "block": trial.block,
        "problem": trial.problem,
        "index": index,
        "step": step,
        "checkpoint": checkpoint,
    }
    if step not in trial.steps:
        return CheckpointAttempt.model_validate(
            {**identity, "checkpoint_state": "not_attempted", "termination": "truncated"}
        )
    return CheckpointAttempt.model_validate(
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
    run_id: str,
    run_dir: Path,
    rule: ProgressionRule,
    max_checkpoints: int | None,
    agent_override: str | None,
) -> TrajectoryRecord:
    """The record of a trial that could not run or be read: every planned step is infra.

    Truncated steps stay ``not_attempted``; everything else is ``infra_error``
    with the exception type, and the trajectory is invalid, so it is excluded
    as infrastructure rather than lost or counted as an agent failure.
    """
    error = type(failure.error).__name__
    reason = f"{RUNNER_EXCEPTION}:{error}"
    attempts = tuple(
        _unrun_attempt(failure.trial, index, step, checkpoint, reason)
        for index, (step, checkpoint) in enumerate(failure.planned_steps)
    )
    reached_vector, source = reached(rule, attempts, max_checkpoints)
    detail = f"{error}: {failure.error}"[:_DETAIL_LIMIT]
    return TrajectoryRecord(
        experiment=experiment,
        run_id=run_id,
        trial=failure.trial,
        trial_dir=_stored_dir(failure.trial_dir, run_dir),
        progression=rule,
        truncated_to=max_checkpoints,
        agent_override=agent_override,
        attempts=attempts,
        reached=reached_vector,
        reached_source=source,
        validity=ExecutionValidity(
            checks=(ValidityCheck(name=RUNNER_EXCEPTION, passed=False, detail=detail),)
        ),
        trial_exception=error,
    )


def write_record(path: Path, record: TrajectoryRecord) -> TrajectoryRecord:
    """Write the record, timing the write itself as the N1 *raidar_result_write* phase."""
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    path.write_text(record.model_dump_json(indent=2) + "\n")
    elapsed = time.perf_counter() - started
    sample = PhaseSample(phase="raidar_result_write", seconds=elapsed)
    final = record.model_copy(update={"phases": (*record.phases, sample)})
    path.write_text(final.model_dump_json(indent=2) + "\n")
    return final
