"""Map a finished Harbor trial to ``StepResult`` records (plan D2).

Per planned checkpoint the state is decided from host-only evidence:

- **not_attempted**: the step was never meant to run: truncated away
  (``max_checkpoints``), or a halt-on-failure run stopped after an earlier
  step's graded verdict missed the halt gate.
- **infra_error**: a step that should have run did not start (the trial
  raised, environment start or step preparation failed, an earlier step
  aborted without a verdict), or the step started but has no trustworthy
  verdict: no oracle
  record (the verifier failed closed, e.g. no pre-agent leak check because the
  guard could not scan), a verifier infrastructure failure, a verifier leak, a
  step exception that is not the agent's own failure (setup.sh, healthcheck,
  API/auth/network errors, the leak guard raising at agent start), or a failed
  component discovery probe (F5, including the per-step reset probe), or an
  agent-end reap that failed or left an agent process alive (F3).
- **solved** / **failed**: otherwise, from the verifier's strict all-cases verdict
  in ``raidar-oracle/steps/<step>/record.json``. Harbor's aggregated trial
  reward is never read.

Component integrity flags (``raidar.subjects.treatments.definition.integrity``: agent tampering
with a component's guardrails) are kept in the step's probe summary and never
change its state or the trial's validity.

The trial's execution-validity gate fails when any step is an infra
error, the trial raised, a halt run did not execute exactly what its gate
allows, or the agent CLI did not run at its pinned version.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.engine.health.liveness import idle_killed_steps
from raidar.engine.health.model import VIOLATION, HealthReport
from raidar.engine.trials.models import (
    CheckpointState,
    ExecutionValidity,
    PlannedTrial,
    ProbeSummary,
    ProgressionRule,
    SnapshotInfo,
    StepResult,
    Usage,
    ValidityCheck,
)
from raidar.engine.trials.progression import derive_reached, gate_passes
from raidar.engine.trials.reaper import load_reap_report, reap_infra_reason
from raidar.engine.trials.timestamps import span_s
from raidar.grading.records import RECORD_FILE, StepRecord, load_step_record, step_record_dir
from raidar.grading.verifier import LEAK_REWARD_KEY
from raidar.subjects.treatments import CompiledCondition
from raidar.subjects.treatments.definition.integrity import condition_step_flags

RESULT_FILE = "result.json"
TRIAL_LOG = "trial.log"
# Step exceptions that are the agent's own outcome: the step is still graded.
# AgentSafetyRefusalError is a model refusal, which Harbor documents as a real
# reward-0 outcome rather than an API fault.
AGENT_FAILURE_EXCEPTIONS = frozenset(
    {
        "AgentTimeoutError",
        "ContextWindowExceededError",
        "OutputTokenExceededError",
        "AgentSafetyRefusalError",
    }
)
# Harbor's installed-agent install commands, as logged in ``trial.log``
# ("Running command: ..."); group 1 is the version installed ("" = latest).
_HARNESS_INSTALL = (
    re.compile(r"npm install -g @openai/codex(?:@(\S+?))?[;\s]"),
    re.compile(r"npm install -g @anthropic-ai/claude-code(?:@(\S+?))?[;\s]"),
    re.compile(r"bootstrap\.sh \| bash -s --(?: (\S+?))?;"),
)
NOT_STARTED = "not_started"
_USAGE_FIELDS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")


def read_result(trial_dir: Path) -> dict[str, Any] | None:
    path = trial_dir / RESULT_FILE
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text())
    return loaded if isinstance(loaded, dict) else None


def step_results(result: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    items = (result or {}).get("step_results") or []
    return {item["step_name"]: item for item in items if isinstance(item, dict)}


def _exception_type(item: Mapping[str, Any] | None) -> str | None:
    info = (item or {}).get("exception_info")
    return info.get("exception_type") if isinstance(info, dict) else None


@dataclass(frozen=True)
class TrialContext:
    """Everything classification needs besides the trial directory."""

    trial: PlannedTrial
    planned_steps: tuple[tuple[str, str], ...]  # (step, checkpoint) for every checkpoint
    task_dir: Path
    compiled: CompiledCondition
    pinned_harness_version: str | None  # None for a no-LLM agent override
    rule: ProgressionRule


def _load_record(trial_dir: Path, step: str) -> StepRecord | None:
    record_dir = step_record_dir(trial_dir, step)
    return load_step_record(record_dir) if (record_dir / RECORD_FILE).is_file() else None


def _infra_reasons(
    record: StepRecord | None,
    exception: str | None,
    probes: ProbeSummary,
    reap: str | None = None,
    idle_killed: bool = False,
) -> list[str]:
    reasons = [reap] if reap else []
    if record is None:
        reasons.append("no_verifier_record" + (f":{exception}" if exception else ""))
    else:
        if record.grade.infra_failure is not None:
            reasons.append(f"verifier_infra:{record.grade.infra_failure.code}")
        if record.rewards.get(LEAK_REWARD_KEY, 0.0) > 0:
            reasons.append("verifier_leak")
    # An enforce-mode idle kill reaches Harbor as a non-zero agent exit recorded on the step
    # like AgentTimeoutError: the agent's own outcome, so the step is graded on what exists.
    if exception is not None and exception not in AGENT_FAILURE_EXCEPTIONS and not idle_killed:
        reasons.append(f"step_exception:{exception}")
    reasons.extend(f"component_probe:{failure}" for failure in probes.failures)
    return reasons


def _usage(item: Mapping[str, Any]) -> Usage | None:
    raw = item.get("agent_result")
    if not isinstance(raw, dict):
        return None
    return Usage(**{name: raw.get(name) for name in _USAGE_FIELDS})


def _instruction_sha(task_dir: Path, step: str) -> str | None:
    path = task_dir / "steps" / step / "instruction.md"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _probe(ctx: TrialContext, trial_dir: Path, step: str, index: int) -> ProbeSummary:
    agent_dir = trial_dir / "steps" / step / "agent"
    report = ctx.compiled.probe_step(agent_dir, index)
    failures = tuple(
        f"{r.component}{'/' + r.slug if r.slug else ''}: {r.detail}" for r in report.failures()
    )
    flags = condition_step_flags(ctx.compiled, agent_dir, index)
    return ProbeSummary(passed=report.passed, failures=failures, flags=flags)


def _not_run(
    ctx: TrialContext, base: Mapping[str, Any], halted: bool, trial_error: str | None
) -> StepResult:
    """A planned checkpoint Harbor never started: by design, or because infrastructure broke."""
    if base["step"] not in ctx.trial.steps:
        reason = "truncated"
    elif halted:
        reason = "halted"
    else:
        cause = f"{NOT_STARTED}:{trial_error}" if trial_error else NOT_STARTED
        return StepResult(
            **base,
            checkpoint_state=CheckpointState.INFRA_ERROR,
            infra_reasons=(cause,),
            termination=NOT_STARTED,
        )
    return StepResult(**base, checkpoint_state=CheckpointState.NOT_ATTEMPTED, termination=reason)


def _step_result(
    ctx: TrialContext,
    trial_dir: Path,
    index: int,
    item: Mapping[str, Any] | None,
    parent: str | None,
    *,
    halted: bool = False,
    trial_error: str | None = None,
) -> StepResult:
    step, checkpoint = ctx.planned_steps[index]
    base: dict[str, Any] = {
        "trial_id": ctx.trial.trial_id,
        "arm": ctx.trial.arm,
        "attempt": ctx.trial.attempt,
        "problem": ctx.trial.problem,
        "index": index,
        "step": step,
        "checkpoint": checkpoint,
        "instruction_sha256": _instruction_sha(ctx.task_dir, step),
    }
    if item is None:
        return _not_run(ctx, base, halted, trial_error)
    record = _load_record(trial_dir, step)
    exception = _exception_type(item)
    probes = _probe(ctx, trial_dir, step, index)
    reap = reap_infra_reason(load_reap_report(step_record_dir(trial_dir, step)))
    reasons = _infra_reasons(record, exception, probes, reap, step in idle_killed_steps(trial_dir))
    if reasons:
        state = CheckpointState.INFRA_ERROR
    else:
        assert record is not None  # no reasons implies a record
        state = CheckpointState.SOLVED if record.grade.solved else CheckpointState.FAILED
    return StepResult(
        **base,
        checkpoint_state=state,
        infra_reasons=tuple(reasons),
        strict_pass_rate=record.grade.pass_rate() if record else None,
        rewards=dict(record.rewards) if record else None,
        verifier_record=(
            step_record_dir(trial_dir, step).relative_to(trial_dir) / RECORD_FILE
        ).as_posix()
        if record
        else None,
        snapshot=SnapshotInfo(path=record.snapshot.stored_path, digest=record.snapshot.digest)
        if record
        else None,
        parent_snapshot_digest=parent,
        trace_dir=f"steps/{step}/agent",
        usage=_usage(item),
        agent_execution_s=span_s(item.get("agent_execution")),
        termination=exception,
        probes=probes,
    )


def _halts(step_result: StepResult, rule: ProgressionRule) -> bool:
    """A graded verdict below the halt gate: later steps are legitimately not run."""
    graded = step_result.checkpoint_state in (CheckpointState.SOLVED, CheckpointState.FAILED)
    return rule.mode == "halt_on_failure" and graded and not gate_passes(step_result.rewards, rule)


def classify_trial(trial_dir: Path, ctx: TrialContext) -> tuple[StepResult, ...]:
    result = read_result(trial_dir)
    items = step_results(result)
    trial_error = _exception_type(result) if result is not None else "no_result"
    results: list[StepResult] = []
    parent: str | None = None
    halted = False
    for index, (step, _) in enumerate(ctx.planned_steps):
        step_result = _step_result(
            ctx,
            trial_dir,
            index,
            items.get(step),
            parent,
            halted=halted,
            trial_error=trial_error,
        )
        if step_result.snapshot is not None:
            parent = step_result.snapshot.digest
        halted = halted or _halts(step_result, ctx.rule)
        results.append(step_result)
    return tuple(results)


def harness_installs(trial_dir: Path) -> list[str]:
    """Versions Harbor installed the agent CLI at during this trial ("" = unpinned/latest)."""
    path = trial_dir / TRIAL_LOG
    if not path.is_file():
        return []
    installs = []
    for line in path.read_text(errors="replace").splitlines():
        if "Running command:" not in line:
            continue
        for pattern in _HARNESS_INSTALL:
            match = pattern.search(line + " ")
            if match:
                installs.append(match.group(1) or "")
    return installs


def _harness_check(
    result: Mapping[str, Any] | None, pinned: str | None, trial_dir: Path
) -> ValidityCheck:
    """The agent CLI ran at the pack pin.

    Harbor reports the configured ``version`` kwarg, not what is installed, and
    a workflow wrapper reports its own version, so the evidence is what Harbor
    installed: an install at any version other than the pin (or unpinned)
    fails. A reinstall at the pin still runs the pinned agent CLI; it is recorded
    in the detail because it means the pack's pre-installed agent CLI was missed.
    """
    if pinned is None:
        return ValidityCheck(
            name="agent_version", passed=True, detail="no-LLM agent override: no agent CLI"
        )
    agent_info = (result or {}).get("agent_info") or {}
    installs = harness_installs(trial_dir)
    off_pin = sorted({version or "latest" for version in installs if version != pinned})
    detail = (
        f"pinned {pinned!r}; agent {agent_info.get('name')!r} reports {agent_info.get('version')!r}"
    )
    if off_pin:
        detail += f"; installed off-pin {off_pin}"
    elif installs:
        detail += "; reinstalled at the pin (pack pre-installed agent CLI missed)"
    return ValidityCheck(name="agent_version", passed=not off_pin, detail=detail)


def _reason_check(name: str, step_results: Sequence[StepResult], prefix: str) -> ValidityCheck:
    hits = [
        f"{step_result.step}: {reason}"
        for step_result in step_results
        for reason in step_result.infra_reasons
        if reason.startswith(prefix)
    ]
    return ValidityCheck(name=name, passed=not hits, detail="; ".join(hits))


def _halt_check(
    step_results: Sequence[StepResult], rule: ProgressionRule, runnable: int | None
) -> ValidityCheck:
    """A halt run executed exactly the checkpoints its own gate allows."""
    if rule.mode != "halt_on_failure":
        return ValidityCheck(name="progression", passed=True, detail="continue")
    executed = tuple(
        a.checkpoint_state is not CheckpointState.NOT_ATTEMPTED and a.termination != NOT_STARTED
        for a in step_results
    )
    allowed = derive_reached([a.rewards for a in step_results], rule, runnable=runnable)
    return ValidityCheck(
        name="progression",
        passed=executed == allowed,
        detail="" if executed == allowed else f"executed {executed}, gate allows {allowed}",
    )


def execution_validity(
    trial_dir: Path,
    step_results: Sequence[StepResult],
    pinned: str | None,
    rule: ProgressionRule,
    *,
    runnable: int | None = None,
) -> ExecutionValidity:
    result = read_result(trial_dir)
    trial_exception = _exception_type(result)
    started = any(a.checkpoint_state is not CheckpointState.NOT_ATTEMPTED for a in step_results)
    checks = [
        ValidityCheck(
            name="trial_completed",
            passed=result is not None and trial_exception is None and started,
            detail="no result.json"
            if result is None
            else (trial_exception or ("" if started else "no step started")),
        ),
        _reason_check("steps_started", step_results, NOT_STARTED),
        _reason_check("verifier_verdicts", step_results, "no_verifier_record"),
        _reason_check("verifier_infra", step_results, "verifier_infra"),
        _reason_check("no_verifier_leak", step_results, "verifier_leak"),
        _reason_check("agent_infra", step_results, "step_exception"),
        _reason_check("component_probes", step_results, "component_probe"),
        _halt_check(step_results, rule, runnable),
        _harness_check(result, pinned, trial_dir),
    ]
    return ExecutionValidity(checks=tuple(checks))


# Health checks that make a trial invalid, grouped under one validity check each. ``H-target``
# is absent on purpose (the target guard already adds ``target_reachable``), as are the L and
# P families and ``unmeasured`` results: an enforce-mode idle kill (``L2`` ``idle_killed``) is
# classified like an agent timeout, so it is a pass with a finding, never a validity failure.
HEALTH_VALIDITY: Mapping[str, tuple[str, ...]] = {
    "health_verdicts": ("V1", "V2", "V3"),
    "health_budgets": ("H1", "H3", "H4", "H5", "H6"),
    "health_tamper": ("T1", "T2", "T3"),
}


def health_validity(report: HealthReport) -> tuple[ValidityCheck, ...]:
    """One validity check per group: failed when any of its health checks is a violation."""
    checks = []
    for name, ids in HEALTH_VALIDITY.items():
        hits = [c for c in report.checks if c.id in ids and c.status == VIOLATION]
        detail = "; ".join(f"{c.id}: {c.detail}" for c in hits)
        checks.append(ValidityCheck(name=name, passed=not hits, detail=detail))
    return tuple(checks)


def trial_exception(trial_dir: Path) -> str | None:
    return _exception_type(read_result(trial_dir))
