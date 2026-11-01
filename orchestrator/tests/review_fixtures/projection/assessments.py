"""Assessments for the synthetic fixture, stored under the keys a real rescore uses.

``correctness@10`` and ``resource-usage@8`` run for real over the synthetic
verifier records and ATIF traces. ``code-quality@9`` needs the provisioned
measurement tools and multi-checkpoint lineage a synthetic snapshot cannot give
meaningful values for, so its result is composed from the fixture's declared
component values, then validated against the scorer's manifest (every metric
and subject present, evidence pointing at real snapshot lines) before it is
stored.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from raidar_contract.canonical import digest_json

from raidar.evaluation.review.projection.assessments import TrialAssessments
from raidar.scoring.code_quality.observations import EXCLUDED_METRIC
from raidar.scoring.correctness.scorer import CorrectnessScorer
from raidar.scoring.framework.builtin_manifests import CODE_QUALITY, CORRECTNESS
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.registry import ScorerRegistration, ScorerRegistry
from raidar.scoring.framework.rescore_config import RescoreConfig
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Evidence,
    Observation,
    Span,
    transition_subject,
)
from raidar.scoring.framework.store import Assessment, ScorerIdentity, assessment_key
from raidar.scoring.framework.trial_inputs import context_inputs
from raidar.scoring.framework.validation import validate_result
from tests.review_fixtures.projection.trials import MemoryTrial

MEASURED_SCORERS = ("correctness@10", "resource-usage@8")
_CHECKPOINT_COMPONENTS = ("verbosity", "erosion", "architecture")
_TRANSITION_COMPONENTS = ("rework", "regression")
_UNMEASURED = "no stored snapshot to measure"


@dataclass(frozen=True)
class LintFinding:
    step: str
    line: int
    message: str


@dataclass(frozen=True)
class QualityPlan:
    """The component values (V, E, H, R, G in [0, 1]) a trial's code is declared to have."""

    values: Mapping[str, float]
    lint: Sequence[LintFinding] = ()


def _ok(metric: str, subject: str, value: float) -> Observation:
    return Observation(metric=metric, subject=subject, status=AssessStatus.OK, value=value)


def _gap(metric: str, subject: str) -> Observation:
    return Observation(
        metric=metric, subject=subject, status=AssessStatus.UNAVAILABLE, reason=_UNMEASURED
    )


SnapshotSource = Callable[[StepInputs], bytes | None]
"""A checkpoint's ``ledger.py`` bytes, or None when it has no stored snapshot."""


def disk_snapshot(step: StepInputs) -> bytes | None:
    return (step.snapshot / "ledger.py").read_bytes() if step.snapshot is not None else None


def _checkpoint(step: StepInputs, plan: QualityPlan, source: SnapshotSource) -> list[Observation]:
    metrics = (*_CHECKPOINT_COMPONENTS, "lint_findings", "production_loc")
    code = source(step)
    if code is None:
        return [_gap(metric, step.step) for metric in metrics]
    lint = sum(1 for finding in plan.lint if finding.step == step.step)
    return [
        *(_ok(metric, step.step, plan.values[metric]) for metric in _CHECKPOINT_COMPONENTS),
        _ok("lint_findings", step.step, lint),
        _ok("production_loc", step.step, len(code.decode("utf-8").splitlines())),
    ]


def _transitions(
    steps: Sequence[StepInputs], plan: QualityPlan, source: SnapshotSource
) -> list[Observation]:
    observations: list[Observation] = []
    for prior, current in zip(steps, steps[1:], strict=False):
        subject = transition_subject(prior.step, current.step)
        measured = source(prior) is not None and source(current) is not None
        observations.extend(
            _ok(metric, subject, plan.values[metric]) if measured else _gap(metric, subject)
            for metric in _TRANSITION_COMPONENTS
        )
        observations.append(
            _ok(EXCLUDED_METRIC, subject, 0.0) if measured else _gap(EXCLUDED_METRIC, subject)
        )
    return observations


def _problem(observations: Sequence[Observation], plan: QualityPlan) -> list[Observation]:
    problem = []
    for metric in (*_CHECKPOINT_COMPONENTS, *_TRANSITION_COMPONENTS):
        subjects = [item for item in observations if item.metric == metric]
        complete = subjects and all(item.status is AssessStatus.OK for item in subjects)
        problem.append(
            _ok(f"problem_{metric}", TRAJECTORY_SUBJECT, plan.values[metric])
            if complete
            else _gap(f"problem_{metric}", TRAJECTORY_SUBJECT)
        )
    return problem


def _evidence(ctx: AssessContext, plan: QualityPlan, source: SnapshotSource) -> list[Evidence]:
    evidence = []
    for finding in plan.lint:
        data = source(ctx.step(finding.step))
        if data is None:
            continue
        evidence.append(
            Evidence(
                metric="lint_findings",
                subject=finding.step,
                path="ledger.py",
                sha256=hashlib.sha256(data).hexdigest(),
                kind="ruff",
                spans=(Span(line=finding.line),),
                message=finding.message,
            )
        )
    return evidence


def declared_quality(ctx: AssessContext, plan: QualityPlan, source: SnapshotSource) -> AssessResult:
    """The code-quality result a trial's declared component values compose to."""
    observations = [item for step in ctx.steps for item in _checkpoint(step, plan, source)]
    observations += _transitions(ctx.steps, plan, source)
    observations += _problem(observations, plan)
    measured = any(item.status is AssessStatus.OK for item in observations)
    return AssessResult(
        status=AssessStatus.OK if measured else AssessStatus.UNAVAILABLE,
        reason=None if measured else _UNMEASURED,
        observations=tuple(observations),
        evidence=tuple(_evidence(ctx, plan, source)) if measured else (),
    )


def quality_result(ctx: AssessContext, plan: QualityPlan) -> AssessResult:
    """The declared result, validated against the scorer's manifest over the stored snapshots."""
    return validate_result(CODE_QUALITY, ctx, declared_quality(ctx, plan, disk_snapshot))


def _memory_context(trial: MemoryTrial, config: Mapping[str, object]) -> AssessContext:
    """The run inputs the real scorers read from a trial directory, held in memory."""
    steps = []
    for result in trial.record.step_results:
        record = trial.step_records.get(result.step)
        if result.step not in trial.snapshots:
            steps.append(
                StepInputs(
                    step=result.step,
                    reached=False,
                    checkpoint=result.checkpoint,
                    snapshot=None,
                    snapshot_digest=None,
                    record=None,
                    record_digest=None,
                    trace_dir=None,
                    trace_digest=None,
                    agent_result=None,
                )
            )
            continue
        steps.append(
            StepInputs(
                step=result.step,
                reached=True,
                checkpoint=result.checkpoint,
                snapshot=None,
                snapshot_digest=trial.snapshots[result.step],
                record=record,
                record_digest=digest_json(record.model_dump(mode="json")) if record else None,
                trace_dir=None,
                trace_digest=digest_json(result.trace_dir),
                agent_result=result.usage.model_dump() if result.usage else None,
                agent_execution_s=result.agent_execution_s,
            )
        )
    return AssessContext(
        problem=trial.record.trial.problem, steps=tuple(steps), config=dict(config)
    )


def _assessment(ref: str, ctx: AssessContext, result: AssessResult) -> Assessment:
    """An Assessment under an in-memory identity (its key still hashes config and run inputs)."""
    identity = ScorerIdentity(ref=ref, manifest_digest="memory", implementation_digest=None)
    config, inputs = dict(ctx.config), context_inputs(ctx)
    key = assessment_key(identity, config=config, tool_lock={}, inputs=inputs)
    return Assessment(
        key=key, scorer=identity, config=config, tool_lock={}, inputs=inputs, result=result
    )


def memory_assessments(
    trial: MemoryTrial, settings: RescoreConfig, quality: QualityPlan
) -> TrialAssessments:
    """What a rescore under ``settings`` would store for the trial: the real correctness scorer
    over the in-memory verifier records, the declared code quality, and a resource summary."""
    correctness_ctx = _memory_context(trial, settings.scorers.get("correctness@10", {}))
    correctness = validate_result(
        CORRECTNESS, correctness_ctx, CorrectnessScorer().assess(correctness_ctx)
    )
    quality_ctx = _memory_context(trial, settings.scorers.get("code-quality@9", {}))

    def ledger(step: StepInputs) -> bytes | None:
        source = trial.sources.get(step.step) if step.reached else None
        return source.encode() if source is not None else None

    resource = AssessResult(
        status=AssessStatus.OK,
        observations=(
            _ok("agent_turns", TRAJECTORY_SUBJECT, float(len(trial.record.step_results))),
            _ok("cost_usd", TRAJECTORY_SUBJECT, 0.01),
        ),
    )
    found = {
        "correctness@10": _assessment("correctness@10", correctness_ctx, correctness),
        "code-quality@9": _assessment(
            "code-quality@9", quality_ctx, declared_quality(quality_ctx, quality, ledger)
        ),
        "resource-usage@8": _assessment("resource-usage@8", correctness_ctx, resource),
    }
    return TrialAssessments(found=found)


def manifest_registry() -> ScorerRegistry:
    """Scorers by manifest only: their keys hash the manifest, not the scorer's source files."""
    return ScorerRegistry(
        ScorerRegistration(manifest=manifest) for manifest in (CORRECTNESS, CODE_QUALITY)
    )
