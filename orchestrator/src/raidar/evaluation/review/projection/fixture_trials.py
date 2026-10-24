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

from raidar.contract.canonical import digest_json
from raidar.contract.snapshot import tree_digest
from raidar.engine.trial.collect import NOT_STARTED
from raidar.engine.trial.models import (
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
from raidar.engine.trial.progression import derive_reached, strict_rule
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
    overlay: tuple[tuple[str, str], ...] = ()
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
    condition_digest: str
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


def _record(
    plan: TrialPlan, trial_dir: Path, checkpoint: CheckpointScript, prior: str | None
) -> StepRecord:
    cases = _cases(checkpoint, prior)
    grade = VerifierGrade(step=checkpoint.step, checkpoint=checkpoint.step, cases=cases)
    stored = f"steps/{checkpoint.step}/artifacts/app"
    record = StepRecord(
        step=checkpoint.step,
        checkpoint=checkpoint.step,
        bundle=BundleRef(
            root=str(plan.bundle), digest=load_bundle(plan.bundle).digest(), problem=plan.problem
        ),
        snapshot=SnapshotRef(
            requested="artifact",
            used="artifact",
            digest=tree_digest(trial_dir / stored),
            stored_path=stored,
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
    write_step_record(trial_dir / "raidar-oracle" / "steps" / checkpoint.step, record)
    return record


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
        steps = [item.step for item in script.checkpoints]
        index = steps.index(checkpoint.step)
        _record(plan, trial_dir, checkpoint, steps[index - 1] if index else None)
    finished = at + timedelta(seconds=4 * (_turns(checkpoint) + 1))
    return {
        "step_name": checkpoint.step,
        "agent_result": _usage(checkpoint).model_dump(),
        "agent_execution": {"started_at": _stamp(at), "finished_at": _stamp(finished)},
    }


def _task(plan: TrialPlan, script: TrialScript) -> None:
    task = plan.job_dir / "tasks" / script.trial_id
    task.mkdir(parents=True, exist_ok=True)
    (task / "task.toml").write_text(
        "[task]\n"
        f'name = "{plan.problem.split("@", 1)[0]}"\n'
        f'description = "{plan.problem_title}"\n\n'
        "[metadata]\n"
        'difficulty = "medium"\n'
        'source = "synthetic"\n',
        encoding="utf-8",
    )
    for checkpoint in script.checkpoints:
        step = task / "steps" / checkpoint.step
        step.mkdir(parents=True, exist_ok=True)
        (step / "instruction.md").write_text(script.prompt + "\n", encoding="utf-8")
        for path, content in script.overlay:
            target = step / "workdir" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


def _state(checkpoint: CheckpointScript) -> CheckpointState:
    if checkpoint.outcome == "infra":
        return CheckpointState.INFRA_ERROR
    return CheckpointState.SOLVED if checkpoint.outcome == "solved" else CheckpointState.FAILED


def _graded(trial_dir: Path, checkpoint: CheckpointScript) -> dict[str, object]:
    """What the verifier record gives the step_result (nothing when the step has no verdict)."""
    if checkpoint.outcome == "infra":
        return {}
    relative = f"raidar-oracle/steps/{checkpoint.step}/record.json"
    rewards = json.loads((trial_dir / relative).read_text())["rewards"]
    return {
        "strict_pass_rate": rewards.get("strict_pass_rate"),
        "rewards": rewards,
        "verifier_record": relative,
    }


def _executed_step(trial_dir: Path, checkpoint: CheckpointScript) -> dict[str, object]:
    """The per-step artifacts of a step Harbor ran."""
    snapshot = f"steps/{checkpoint.step}/artifacts/app"
    return {
        "snapshot": SnapshotInfo(path=snapshot, digest=tree_digest(trial_dir / snapshot)),
        "trace_dir": f"steps/{checkpoint.step}/agent",
        "usage": _usage(checkpoint),
        "agent_execution_s": 4.0 * (_turns(checkpoint) + 1),
    }


def _step_result(
    plan: TrialPlan, script: TrialScript, index: int, checkpoint: CheckpointScript, ran: bool
) -> StepResult:
    trial_dir = plan.job_dir / "trials" / script.trial_id
    graded = _graded(trial_dir, checkpoint)
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
            "infra_reasons": () if graded else _infra_reasons(ran),
            "termination": None if ran else NOT_STARTED,
            **graded,
            **(_executed_step(trial_dir, checkpoint) if ran else {}),
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
        "component_probes": None,
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
        "arm": digest_json([script.arm, plan.condition_digest, plan.agent_digest]),
        "condition": plan.condition_digest,
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
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "started_at": _stamp(script.started),
                "finished_at": _stamp(at),
                "step_results": steps,
                "exception_info": _exception_info(script, at),
            },
            indent=2,
        )
    )
    _task(plan, script)
    ran = len(steps)
    step_results = tuple(
        _step_result(plan, script, index, checkpoint, index < ran)
        for index, checkpoint in enumerate(script.checkpoints)
    )
    rewards = [step_result.rewards for step_result in step_results]
    record = TrialRecord(
        experiment=plan.experiment,
        job_id=plan.job_id,
        trial=_planned(plan, script),
        trial_dir=f"trials/{script.trial_id}",
        progression=_CONTINUE,
        step_results=step_results,
        reached=derive_reached(rewards, strict_rule(_CONTINUE)),
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
    path = plan.job_dir / "trial-records" / f"{script.trial_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(indent=2) + "\n")
    return record
