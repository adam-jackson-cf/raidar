"""Synthetic experiment-epoch trial artifacts, written with the production models.

Every file a real ``raidar run`` leaves for review is written here from the
same models (Harbor ATIF, verifier bundle and step records, ``TrialRecord``),
so the review projection and the real scorers read the fixture exactly as they
read a real run. Nothing here is real benchmark evidence: the fixture's runs are
marked ``synthetic`` and its problems live under ``synthetic/``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from harbor.models.trajectories import (
    Agent,
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)
from raidar_contract.canonical import digest_json

from raidar.contract.snapshot import tree_digest
from raidar.contract.trial import (
    NOT_STARTED,
    CheckpointState,
    ExecutionValidity,
    PhaseSample,
    PlannedTrial,
    ProgressionRule,
    SnapshotInfo,
    StepResult,
    TrialHashes,
    TrialRecord,
    Usage,
    ValidityCheck,
)
from raidar.engine.trial.progression import STRICT_RULE, derive_reached
from raidar.grading.bundle import BundleManifest, GraderSpec, VerifierStepSpec, load_bundle
from raidar.grading.models import CaseGroup, CaseResult, CaseStatus, VerifierGrade
from raidar.grading.records import (
    BundleRef,
    CoverageRef,
    GraderRef,
    SnapshotRef,
    StepRecord,
    write_step_record,
)

Outcome = Literal["solved", "failed", "infra"]
HARNESS_EXIT = "NonZeroAgentExitCodeError"
_CONTINUE = ProgressionRule(mode="continue", reward_key="strict_pass_rate")
_TOKENS_PER_TURN = (6_000, 700, 4_000)


@dataclass(frozen=True)
class CheckpointScript:
    step: str
    outcome: Outcome
    passed: int
    total: int
    source: str
    commands: tuple[tuple[str, int], ...]
    message: str


@dataclass(frozen=True)
class TrialScript:
    trial_id: str
    arm: str
    attempt: int
    model: str
    started: datetime
    prompt: str
    checkpoints: tuple[CheckpointScript, ...]
    failure: str | None = None


@dataclass(frozen=True)
class TrialPlan:
    """Experiment-level identities a trial is written under."""

    experiment: str
    job_id: str
    job_dir: Path
    problem: str
    problem_title: str
    bundle: Path
    lock_sha256: str
    workflow_digest: str
    packages_digest: str
    primitive_code_digest: str
    agent_digest: str


def write_bundle(root: Path, problem: str, steps: Sequence[str]) -> Path:
    """A host-only verifier bundle with one hidden test file per checkpoint."""
    tests = root / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    specs = []
    for step in steps:
        name = f"test_{step}.py"
        (tests / name).write_text(f"def test_{step}():\n    assert True\n", encoding="utf-8")
        specs.append(
            VerifierStepSpec(
                name=step, checkpoint=step, test_files=(name,), pytest_args=(f"{{tests}}/{name}",)
            )
        )
    manifest = BundleManifest(
        problem=problem, grader=GraderSpec(base_image="python:3.12-slim"), steps=tuple(specs)
    )
    (root / "oracle-bundle.json").write_text(manifest.model_dump_json(indent=2) + "\n")
    return load_bundle(root).root


def _stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _atif(script: TrialScript, checkpoint: CheckpointScript, started: datetime) -> Trajectory:
    steps = [
        Step(step_id=1, timestamp=_stamp(started), source="user", message=script.prompt),
    ]
    prompt, completion, cached = _TOKENS_PER_TURN
    for index, (command, code) in enumerate(checkpoint.commands):
        call = ToolCall(
            tool_call_id=f"call-{index + 1}",
            function_name="exec_command",
            arguments={"cmd": command, "workdir": "/app"},
        )
        output = f"Process exited with code {code}\n{command}: {'ok' if code == 0 else 'failed'}"
        steps.append(
            Step(
                step_id=index + 2,
                timestamp=_stamp(started + timedelta(seconds=4 * (index + 1))),
                source="agent",
                model_name=script.model,
                message=f"Running `{command}` against the ledger module.",
                tool_calls=[call],
                observation=Observation(
                    results=[ObservationResult(source_call_id=call.tool_call_id, content=output)]
                ),
                metrics=Metrics(
                    prompt_tokens=prompt, completion_tokens=completion, cached_tokens=cached
                ),
                llm_call_count=1,
            )
        )
    steps.append(
        Step(
            step_id=len(steps) + 1,
            timestamp=_stamp(started + timedelta(seconds=4 * (len(checkpoint.commands) + 1))),
            source="agent",
            model_name=script.model,
            message=checkpoint.message,
            metrics=Metrics(
                prompt_tokens=prompt, completion_tokens=completion, cached_tokens=cached
            ),
            llm_call_count=1,
        )
    )
    return Trajectory(
        schema_version="ATIF-v1.7",
        session_id=f"{script.trial_id}-{checkpoint.step}",
        agent=Agent(name="synthetic", version="0.0.0", model_name=script.model),
        steps=steps,
    )


def _turns(checkpoint: CheckpointScript) -> int:
    return len(checkpoint.commands) + 1


def _usage(checkpoint: CheckpointScript) -> Usage:
    prompt, completion, cached = _TOKENS_PER_TURN
    turns = _turns(checkpoint)
    return Usage(
        n_input_tokens=prompt * turns,
        n_cache_tokens=cached * turns,
        n_output_tokens=completion * turns,
        cost_usd=round(0.002 * turns, 6),
    )


def _cases(checkpoint: CheckpointScript, prior: str | None) -> tuple[CaseResult, ...]:
    """Passing cases are the checkpoint's own; failing ones re-run an earlier checkpoint's."""
    cases = []
    for index in range(checkpoint.total):
        passed = index < checkpoint.passed
        regression = not passed and prior is not None
        cases.append(
            CaseResult(
                id=f"tests/test_{checkpoint.step}.py::test_case_{index}",
                group=CaseGroup.REGRESSION
                if regression
                else (CaseGroup.CORE if index % 2 == 0 else CaseGroup.FUNCTIONALITY),
                checkpoint_origin=prior if regression and prior else checkpoint.step,
                status=CaseStatus.PASSED if passed else CaseStatus.FAILED,
            )
        )
    return tuple(cases)


def step_record(
    plan: TrialPlan,
    checkpoint: CheckpointScript,
    prior: str | None,
    *,
    bundle_digest: str,
    snapshot_digest: str,
) -> StepRecord:
    """The verifier's record of one graded checkpoint (identities are the caller's)."""
    cases = _cases(checkpoint, prior)
    grade = VerifierGrade(step=checkpoint.step, checkpoint=checkpoint.step, cases=cases)
    return StepRecord(
        step=checkpoint.step,
        checkpoint=checkpoint.step,
        bundle=BundleRef(root=str(plan.bundle), digest=bundle_digest, problem=plan.problem),
        snapshot=SnapshotRef(
            requested="artifact",
            used="artifact",
            digest=snapshot_digest,
            stored_path=_snapshot_path(checkpoint),
        ),
        grader=GraderRef(
            image="raidar-grader:synthetic",
            environment_id="synthetic",
            image_built=False,
            timeout_s=900.0,
        ),
        grade=grade,
        summary=grade.summary(),
        coverage=CoverageRef(enabled=False),
        rewards=grade.rewards(),
    )


def _snapshot_path(checkpoint: CheckpointScript) -> str:
    return f"steps/{checkpoint.step}/artifacts/app"


def _prior(script: TrialScript, checkpoint: CheckpointScript) -> str | None:
    steps = [item.step for item in script.checkpoints]
    index = steps.index(checkpoint.step)
    return steps[index - 1] if index else None


def _record(
    plan: TrialPlan, trial_dir: Path, checkpoint: CheckpointScript, prior: str | None
) -> StepRecord:
    record = step_record(
        plan,
        checkpoint,
        prior,
        bundle_digest=load_bundle(plan.bundle).digest(),
        snapshot_digest=tree_digest(trial_dir / _snapshot_path(checkpoint)),
    )
    write_step_record(trial_dir / "raidar-oracle" / "steps" / checkpoint.step, record)
    return record


def _step_timing(checkpoint: CheckpointScript, at: datetime) -> dict[str, object]:
    """Harbor's per-step usage and agent timing for a step started at ``at``."""
    finished = at + timedelta(seconds=4 * (_turns(checkpoint) + 1))
    return {
        "step_name": checkpoint.step,
        "agent_result": _usage(checkpoint).model_dump(),
        "agent_execution": {"started_at": _stamp(at), "finished_at": _stamp(finished)},
    }


def _write_step(
    plan: TrialPlan,
    script: TrialScript,
    trial_dir: Path,
    checkpoint: CheckpointScript,
    at: datetime,
) -> dict[str, object]:
    app = trial_dir / "steps" / checkpoint.step / "artifacts" / "app"
    app.mkdir(parents=True, exist_ok=True)
    (app / "ledger.py").write_text(checkpoint.source, encoding="utf-8")
    agent = trial_dir / "steps" / checkpoint.step / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    document = _atif(script, checkpoint, at)
    (agent / "trajectory.json").write_text(document.model_dump_json(indent=2, exclude_none=True))
    if checkpoint.outcome != "infra":
        _record(plan, trial_dir, checkpoint, _prior(script, checkpoint))
    return _step_timing(checkpoint, at)


def _task_toml(plan: TrialPlan) -> str:
    return (
        "[task]\n"
        f'name = "{plan.problem.split("@", 1)[0]}"\n'
        f'description = "{plan.problem_title}"\n\n'
        "[metadata]\n"
        'difficulty = "medium"\n'
        'source = "synthetic"\n'
    )


def _task(plan: TrialPlan, script: TrialScript) -> None:
    task = plan.job_dir / "tasks" / script.trial_id
    task.mkdir(parents=True, exist_ok=True)
    (task / "task.toml").write_text(_task_toml(plan), encoding="utf-8")
    for checkpoint in script.checkpoints:
        step = task / "steps" / checkpoint.step
        step.mkdir(parents=True, exist_ok=True)
        (step / "instruction.md").write_text(script.prompt + "\n", encoding="utf-8")


def _state(checkpoint: CheckpointScript) -> CheckpointState:
    if checkpoint.outcome == "infra":
        return CheckpointState.INFRA_ERROR
    return CheckpointState.SOLVED if checkpoint.outcome == "solved" else CheckpointState.FAILED


def _graded_from(checkpoint: CheckpointScript, rewards: dict[str, float]) -> dict[str, object]:
    """What a verifier record's rewards give the step_result."""
    return {
        "strict_pass_rate": rewards.get("strict_pass_rate"),
        "rewards": rewards,
        "verifier_record": f"raidar-oracle/steps/{checkpoint.step}/record.json",
    }


def _graded(trial_dir: Path, checkpoint: CheckpointScript) -> dict[str, object]:
    """What the verifier record gives the step_result (nothing when the step has no verdict)."""
    if checkpoint.outcome == "infra":
        return {}
    relative = f"raidar-oracle/steps/{checkpoint.step}/record.json"
    return _graded_from(checkpoint, json.loads((trial_dir / relative).read_text())["rewards"])


def _executed_from(checkpoint: CheckpointScript, snapshot_digest: str) -> dict[str, object]:
    """The per-step artifacts of a step Harbor ran."""
    return {
        "snapshot": SnapshotInfo(path=_snapshot_path(checkpoint), digest=snapshot_digest),
        "trace_dir": f"steps/{checkpoint.step}/agent",
        "usage": _usage(checkpoint),
        "agent_execution_s": 4.0 * (_turns(checkpoint) + 1),
    }


def _executed_step(trial_dir: Path, checkpoint: CheckpointScript) -> dict[str, object]:
    """The per-step artifacts of a step Harbor ran, on disk."""
    return _executed_from(checkpoint, tree_digest(trial_dir / _snapshot_path(checkpoint)))


def _step_result(
    plan: TrialPlan,
    script: TrialScript,
    index: int,
    checkpoint: CheckpointScript,
    graded: dict[str, object],
    executed: dict[str, object] | None,
) -> StepResult:
    """``executed`` is None for a step Harbor never started."""
    return StepResult.model_validate(
        {
            "trial_id": script.trial_id,
            "arm": script.arm,
            "attempt": script.attempt,
            "problem": plan.problem,
            "index": index,
            "step": checkpoint.step,
            "checkpoint": checkpoint.step,
            "checkpoint_state": _state(checkpoint),
            "infra_reasons": () if graded else _infra_reasons(executed is not None),
            "termination": None if executed is not None else NOT_STARTED,
            **graded,
            **(executed or {}),
        }
    )


def _infra_reasons(ran: bool) -> tuple[str, ...]:
    """The execution driver's codes for a harness that exited before verification."""
    if not ran:
        return (f"{NOT_STARTED}:{HARNESS_EXIT}",)
    return (f"no_verifier_record:{HARNESS_EXIT}", f"step_exception:{HARNESS_EXIT}")


def _reason_detail(step_results: Sequence[StepResult], prefix: str) -> str | None:
    hits = [
        f"{step_result.step}: {reason}"
        for step_result in step_results
        for reason in step_result.infra_reasons
        if reason.startswith(prefix)
    ]
    return "; ".join(hits) or None


def _validity(script: TrialScript, step_results: Sequence[StepResult]) -> ExecutionValidity:
    checks = {
        "trial_completed": HARNESS_EXIT if script.failure else None,
        "steps_started": _reason_detail(step_results, NOT_STARTED),
        "verifier_verdicts": _reason_detail(step_results, "no_verifier_record"),
        "verifier_infra": None,
        "no_verifier_leak": None,
        "agent_infra": _reason_detail(step_results, "step_exception"),
        "lab_checks": None,
        "progression": None,
        "agent_version": None,
    }
    return ExecutionValidity(
        checks=tuple(
            ValidityCheck(name=name, passed=detail is None, detail=detail or "")
            for name, detail in checks.items()
        )
    )


def _planned(plan: TrialPlan, script: TrialScript) -> PlannedTrial:
    hashes = {
        "lock": plan.lock_sha256,
        "problem": digest_json(plan.problem),
        "arm": digest_json(
            [script.arm, plan.workflow_digest, plan.primitive_code_digest, plan.agent_digest]
        ),
        "workflow": plan.workflow_digest,
        "packages": plan.packages_digest,
        "primitive_code": plan.primitive_code_digest,
        "agent": plan.agent_digest,
        "protocol": digest_json(_CONTINUE.model_dump(mode="json")),
    }
    return PlannedTrial(
        trial_id=script.trial_id,
        attempt=script.attempt,
        order=0,
        sequence=script.attempt,
        arm=script.arm,
        problem=plan.problem,
        problem_index=0,
        steps=tuple(checkpoint.step for checkpoint in script.checkpoints),
        hashes=TrialHashes(**hashes, trial=digest_json([hashes, script.trial_id])),
    )


def _exception_info(script: TrialScript, at: datetime) -> dict[str, str] | None:
    """Harbor's ``ExceptionInfo`` for a trial whose agent exited (None otherwise)."""
    if script.failure is None:
        return None
    return {
        "exception_type": HARNESS_EXIT,
        "exception_message": script.failure,
        "exception_traceback": "",
        "occurred_at": _stamp(at),
    }


def _executed(script: TrialScript) -> list[CheckpointScript]:
    """Checkpoints Harbor ran: every one up to (and including) an infrastructure failure."""
    executed = []
    for checkpoint in script.checkpoints:
        executed.append(checkpoint)
        if checkpoint.outcome == "infra":
            break
    return executed


def _trial_record(
    plan: TrialPlan, script: TrialScript, step_results: tuple[StepResult, ...]
) -> TrialRecord:
    rewards = [step_result.rewards for step_result in step_results]
    return TrialRecord(
        experiment=plan.experiment,
        job_id=plan.job_id,
        trial=_planned(plan, script),
        trial_dir=f"trials/{script.trial_id}",
        progression=_CONTINUE,
        step_results=step_results,
        reached=derive_reached(rewards, STRICT_RULE),
        reached_source="derived",
        validity=_validity(script, step_results),
        phases=(
            PhaseSample(phase="environment_start", seconds=4.0, condition="warm"),
            PhaseSample(phase="agent_setup", seconds=2.0),
            *(
                PhaseSample(
                    phase="agent_execution",
                    seconds=step_result.agent_execution_s or 0.0,
                    step=step_result.step,
                )
                for step_result in step_results
            ),
        ),
        trial_exception=HARNESS_EXIT if script.failure else None,
    )


def _result_json(script: TrialScript, steps: list[dict[str, object]], finished: datetime) -> str:
    """Harbor's ``result.json`` of the trial."""
    return json.dumps(
        {
            "started_at": _stamp(script.started),
            "finished_at": _stamp(finished),
            "step_results": steps,
            "exception_info": _exception_info(script, finished),
        },
        indent=2,
    )


def write_trial(plan: TrialPlan, script: TrialScript) -> TrialRecord:
    """Write one synthetic trial (trial dir, task dir, trial record) and return its record."""
    trial_dir = plan.job_dir / "trials" / script.trial_id
    steps, at = [], script.started
    for checkpoint in _executed(script):
        steps.append(_write_step(plan, script, trial_dir, checkpoint, at))
        at += timedelta(seconds=4 * (_turns(checkpoint) + 2))
    (trial_dir / "config.json").write_text(
        json.dumps({"verifier": {"kwargs": {"oracle_bundle": str(plan.bundle)}}}, indent=2)
    )
    (trial_dir / "result.json").write_text(_result_json(script, steps, at))
    _task(plan, script)
    ran = len(steps)
    step_results = tuple(
        _step_result(
            plan,
            script,
            index,
            checkpoint,
            _graded(trial_dir, checkpoint),
            _executed_step(trial_dir, checkpoint) if index < ran else None,
        )
        for index, checkpoint in enumerate(script.checkpoints)
    )
    record = _trial_record(plan, script, step_results)
    path = plan.job_dir / "trial-records" / f"{script.trial_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(indent=2) + "\n")
    return record


@dataclass(frozen=True)
class MemoryTrial:
    """A synthetic trial held in memory: its record, the files a review reads and its evidence."""

    record: TrialRecord
    files: dict[Path, str]
    """UTF-8 files of the run a review reads (Harbor result, ATIF, verifier records, task)."""
    step_records: dict[str, StepRecord]
    """The verifier record of each graded checkpoint."""
    snapshots: dict[str, str]
    """Each executed checkpoint's snapshot digest."""
    sources: dict[str, str]
    """Each executed checkpoint's ``ledger.py`` text."""


def memory_trial(plan: TrialPlan, script: TrialScript) -> MemoryTrial:
    """The trial ``write_trial`` would write, without touching disk (under ``plan.job_dir``)."""
    trial_dir = plan.job_dir / "trials" / script.trial_id
    task = plan.job_dir / "tasks" / script.trial_id / "task.toml"
    files: dict[Path, str] = {task: _task_toml(plan)}
    steps: list[dict[str, object]] = []
    records: dict[str, StepRecord] = {}
    snapshots: dict[str, str] = {}
    sources: dict[str, str] = {}
    at = script.started
    for checkpoint in _executed(script):
        trace = trial_dir / f"steps/{checkpoint.step}/agent/trajectory.json"
        files[trace] = _atif(script, checkpoint, at).model_dump_json(indent=2, exclude_none=True)
        snapshots[checkpoint.step] = digest_json(checkpoint.source)
        sources[checkpoint.step] = checkpoint.source
        if checkpoint.outcome != "infra":
            record = step_record(
                plan,
                checkpoint,
                _prior(script, checkpoint),
                bundle_digest=digest_json(plan.problem),
                snapshot_digest=snapshots[checkpoint.step],
            )
            records[checkpoint.step] = record
            files[trial_dir / "raidar-oracle" / "steps" / checkpoint.step / "record.json"] = (
                record.model_dump_json(indent=2) + "\n"
            )
        steps.append(_step_timing(checkpoint, at))
        at += timedelta(seconds=4 * (_turns(checkpoint) + 2))
    files[trial_dir / "result.json"] = _result_json(script, steps, at)
    step_results = tuple(
        _step_result(
            plan,
            script,
            index,
            checkpoint,
            _graded_from(checkpoint, records[checkpoint.step].rewards)
            if checkpoint.step in records
            else {},
            _executed_from(checkpoint, snapshots[checkpoint.step])
            if checkpoint.step in snapshots
            else None,
        )
        for index, checkpoint in enumerate(script.checkpoints)
    )
    record = _trial_record(plan, script, step_results)
    return MemoryTrial(record, files, records, snapshots, sources)
