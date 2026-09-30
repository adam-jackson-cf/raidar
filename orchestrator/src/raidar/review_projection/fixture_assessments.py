"""Assessments for the synthetic fixture, stored under the keys a real rescore uses.

``correctness@1`` and ``resource-usage@1`` run for real over the synthetic
oracle records and ATIF traces. ``code-quality@1`` needs the provisioned
measurement tools and multi-checkpoint lineage a synthetic snapshot cannot give
meaningful values for, so its result is composed from the fixture's declared
component values, then validated against the scorer's manifest (every metric
and subject present, evidence pointing at real snapshot lines) before it is
stored.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from raidar.scorers.builtin_manifests import CODE_QUALITY
from raidar.scorers.catalog import ScorerRegistry
from raidar.scorers.keys import store_key
from raidar.scorers.rescore import assess
from raidar.scorers.rescore_config import RescoreConfig
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Evidence,
    Observation,
    Span,
    transition_subject,
)
from raidar.scorers.sdk.validation import validate_result
from raidar.scorers.store import Assessment, AssessmentStore
from raidar.scorers.trial_inputs import context_inputs, load_trial_context

MEASURED_SCORERS = ("correctness@1", "resource-usage@1")
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


def _checkpoint(step: StepInputs, plan: QualityPlan) -> list[Observation]:
    metrics = (*_CHECKPOINT_COMPONENTS, "lint_findings", "production_loc")
    if step.snapshot is None:
        return [_gap(metric, step.step) for metric in metrics]
    source = (step.snapshot / "ledger.py").read_text(encoding="utf-8")
    lint = sum(1 for finding in plan.lint if finding.step == step.step)
    return [
        *(_ok(metric, step.step, plan.values[metric]) for metric in _CHECKPOINT_COMPONENTS),
        _ok("lint_findings", step.step, lint),
        _ok("production_loc", step.step, len(source.splitlines())),
    ]


def _transitions(steps: Sequence[StepInputs], plan: QualityPlan) -> list[Observation]:
    observations: list[Observation] = []
    for prior, current in zip(steps, steps[1:], strict=False):
        subject = transition_subject(prior.step, current.step)
        measured = prior.snapshot is not None and current.snapshot is not None
        observations.extend(
            _ok(metric, subject, plan.values[metric]) if measured else _gap(metric, subject)
            for metric in _TRANSITION_COMPONENTS
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


def _evidence(ctx: AssessContext, plan: QualityPlan) -> list[Evidence]:
    evidence = []
    for finding in plan.lint:
        snapshot = ctx.step(finding.step).snapshot
        if snapshot is None:
            continue
        data = (snapshot / "ledger.py").read_bytes()
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


def quality_result(ctx: AssessContext, plan: QualityPlan) -> AssessResult:
    observations = [item for step in ctx.steps for item in _checkpoint(step, plan)]
    observations += _transitions(ctx.steps, plan)
    observations += _problem(observations, plan)
    measured = any(item.status is AssessStatus.OK for item in observations)
    result = AssessResult(
        status=AssessStatus.OK if measured else AssessStatus.UNAVAILABLE,
        reason=None if measured else _UNMEASURED,
        observations=tuple(observations),
        evidence=tuple(_evidence(ctx, plan)) if measured else (),
    )
    return validate_result(CODE_QUALITY, ctx, result)


def store_assessments(
    store: AssessmentStore,
    registry: ScorerRegistry,
    trial_dir: Path,
    settings: RescoreConfig,
    plan: QualityPlan,
) -> None:
    """Store the fixture trial's Assessments exactly where ``raidar assess --rescore`` looks."""
    ctx = load_trial_context(trial_dir, inventory_policy=settings.inventory_policy)
    for ref in MEASURED_SCORERS:
        scorer_ctx = replace(ctx, config=dict(settings.scorers.get(ref, {})))
        assess(store, registry.get(ref), scorer_ctx, trial=trial_dir.name)
    parts = store_key(
        registry.get(CODE_QUALITY.ref),
        settings.scorers.get(CODE_QUALITY.ref, {}),
        context_inputs(ctx),
    )
    store.put(
        Assessment(
            key=parts.key,
            scorer=parts.identity,
            config=parts.config,
            tool_lock=parts.tool_lock,
            inputs=parts.inputs,
            result=quality_result(ctx, plan),
        )
    )
