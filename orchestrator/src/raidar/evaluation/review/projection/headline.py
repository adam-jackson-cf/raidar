"""The experiment's headline metric applied to one trial's Assessments.

``composite_score`` is S/100 under the headline metric (``scb-canonical@1`` or
its one-shot variant), so the review surface's delivery tiers keep their
meaning: S is correctness-gated. ``quality_score`` is the policy's inertia I and
``diagnostic_score`` is correctness C. C is the policy's definition (the SCB
fork's ``C_p``, ``analysis.assessed.correctness_component``): the mean of
``correctness@1``'s per-checkpoint ``strict_pass_rate``, an unreached
checkpoint counting 0. V/E/H/R/G come from ``code-quality@3``'s problem-level
means. A component that is not ``ok`` makes the score ineligible with the
scorer's own reason, and a scorer with no stored Assessment makes it
unassessed; neither is ever read as zero. Under a policy with the
``zero_correctness`` rule (``@2``), a measured C = 0 scores S = 0 regardless.

Two trial states withhold the headline even when every component is
measured, exactly as ``raidar analyze`` excludes them (``analysis.assessed``):
an infra-invalid execution, and a ``max_checkpoints`` truncation, whose K is
not the problem's K. Their components stay visible; S, C and I do not.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from raidar.design.experiments.metrics import (
    CORRECTNESS,
    DEFAULT_METRIC_REGISTRY,
    Metric,
    MetricScore,
)
from raidar.design.experiments.references import RegistryReferenceError
from raidar.engine.trials.models import CheckpointState, TrialRecord
from raidar.evaluation.analysis.assessed import (
    CODE_QUALITY_REF,
    Observed,
    correctness_component,
    infra_reasons,
)
from raidar.evaluation.analysis.outcomes import CORRECTNESS_REF
from raidar.evaluation.analysis.policy_inputs import ComponentInput, score_inputs
from raidar.evaluation.review.projection.assessments import TrialAssessments
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus, Observation

COMPONENT_METRICS = {
    "V": "problem_verbosity",
    "E": "problem_erosion",
    "H": "problem_architecture",
    "R": "problem_rework",
    "G": "problem_regression",
}
COMPONENT_NAMES = {
    "C": "correctness",
    "V": "verbosity",
    "E": "erosion",
    "H": "architecture",
    "R": "rework",
    "G": "regression breadth",
}


@dataclass(frozen=True)
class Headline:
    policy: str
    checkpoints: int
    score: MetricScore | None
    correctness: Decimal | None
    components: Mapping[str, Observation] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    withheld: tuple[str, ...] = ()

    @property
    def composite(self) -> float | None:
        if self.withheld or self.score is None or self.score.score is None:
            return None
        return float(self.score.score / 100)

    @property
    def quality(self) -> float | None:
        if self.withheld or self.score is None or self.score.inertia is None:
            return None
        return float(self.score.inertia)

    @property
    def diagnostic(self) -> float | None:
        return None if self.withheld or self.correctness is None else float(self.correctness)


def withheld_reasons(record: TrialRecord) -> tuple[str, ...]:
    """Why this trial has no headline even with every component measured."""
    reasons = [
        f"Execution invalid: {check.name}: {check.detail or 'failed'}"
        for check in record.validity.checks
        if not check.passed
    ]
    reasons += [
        f"Execution invalid: {step_result.step} infra error: "
        + (", ".join(step_result.infra_reasons) or "infra_error")
        for step_result in record.step_results
        if step_result.checkpoint_state is CheckpointState.INFRA_ERROR
    ]
    if not reasons and (infra_reasons(record) or not record.valid):
        reasons.append("Execution invalid: the trial raised an exception")
    if record.truncated_to is not None:
        reasons.append(
            f"Truncated run: only the first {record.truncated_to} checkpoint(s) ran "
            "(max_checkpoints); the problem-level score needs every checkpoint"
        )
    return tuple(reasons)


def trial_observation(
    assessments: TrialAssessments, scorer: str, metric: str
) -> Observation | None:
    assessment = assessments.get(scorer)
    if assessment is None:
        return None
    return next(
        (
            item
            for item in assessment.result.observations
            if item.metric == metric and item.subject == TRAJECTORY_SUBJECT
        ),
        None,
    )


def _unmeasured(observation: Observation | None, missing: str | None) -> str:
    if observation is None:
        return missing or "the scorer reported no problem-level value"
    return f"{observation.status.value}: {observation.reason}"


def _correctness(
    assessments: TrialAssessments, steps: tuple[str, ...]
) -> tuple[Decimal | None, Observation | None, str | None]:
    """C over ``steps`` and the observation that stands for it in the panel.

    The observation is a trial-level C built from the per-checkpoint pass
    rates (status ``ok``), or the first per-checkpoint gap.
    """
    assessment = assessments.get(CORRECTNESS_REF)
    if assessment is None:
        return None, None, _unmeasured(None, assessments.missing.get(CORRECTNESS_REF))
    value = correctness_component(Observed(CORRECTNESS_REF, assessment.result), steps)
    if isinstance(value, Decimal):
        observation = Observation(
            metric="correctness",
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.OK,
            value=float(value),
        )
        return value, observation, None
    observation = Observation(
        metric="correctness",
        subject=TRAJECTORY_SUBJECT,
        status=AssessStatus(value.status),
        reason=value.reason,
    )
    return None, observation, _unmeasured(observation, None)


def _quality_inputs(
    policy: Metric, assessments: TrialAssessments
) -> tuple[dict[str, ComponentInput], dict[str, Observation]]:
    inputs: dict[str, ComponentInput] = {}
    used: dict[str, Observation] = {}
    for component in policy.inertia_weights:
        observation = trial_observation(assessments, CODE_QUALITY_REF, COMPONENT_METRICS[component])
        if observation is not None:
            used[component] = observation
        if observation is not None and observation.status is AssessStatus.OK:
            inputs[component] = ComponentInput(value=float(observation.value or 0.0))
        else:
            missing = assessments.missing.get(CODE_QUALITY_REF)
            inputs[component] = ComponentInput(gap=_unmeasured(observation, missing))
    return inputs, used


def _reason_text(policy: str, component: str | None, detail: str) -> str:
    label = f"{component} ({COMPONENT_NAMES[component]})" if component else "policy"
    return f"{policy} ineligible: {label}: {detail}"


UNDECLARED_MARKER = "which the experiment does not declare"


def needed_scorers(policy: Metric) -> tuple[str, ...]:
    """The scorers whose Assessments the policy's components come from."""
    if not policy.inertia_weights:
        return (CORRECTNESS_REF,)
    return (CORRECTNESS_REF, CODE_QUALITY_REF)


def _undeclared_reasons(policy: Metric, declared: Collection[str] | None) -> tuple[str, ...]:
    if declared is None:
        return ()
    return tuple(
        f"{policy.ref} not computable: the headline metric needs {ref}, "
        f"{UNDECLARED_MARKER} (add it to assessment.scorers and relock)"
        for ref in needed_scorers(policy)
        if ref not in declared
    )


def _policy_reasons(
    policy: Metric,
    score: MetricScore,
    assessments: TrialAssessments,
    declared: Collection[str] | None = None,
) -> tuple[str, ...]:
    """Why S is missing. A scorer the experiment never declared is the real cause and is
    named first (no rescore or rerun can fix it); then unassessed, which takes precedence
    over ineligible: "not scored yet" is not "not scorable"."""
    undeclared = _undeclared_reasons(policy, declared)
    if undeclared:
        return undeclared
    unassessed = [ref for ref in (CORRECTNESS_REF, CODE_QUALITY_REF) if ref in assessments.missing]
    if unassessed:
        return tuple(
            f"{policy.ref} unassessed: {ref}: {assessments.missing[ref]}" for ref in unassessed
        )
    return tuple(
        _reason_text(policy.ref, reason.component, reason.detail) for reason in score.reasons
    )


def policy_shares(policy: Metric) -> tuple[float, float]:
    """The policy's (base, inertia) shares of S, read from the policy itself.

    S = 100·C·(base + inertia·I), so with C=1 the score at I=0 is 100·base and
    the score at I=1 is 100·(base + inertia).
    """
    k = policy.min_checkpoints
    ones = {CORRECTNESS: 1, **dict.fromkeys(policy.inertia_weights, 1)}
    zeros = {CORRECTNESS: 1, **dict.fromkeys(policy.inertia_weights, 0)}
    low = policy.score(checkpoints=k, components=zeros).score
    high = policy.score(checkpoints=k, components=ones).score
    if low is None or high is None:
        raise ValueError(f"{policy.ref} does not score a fully measured trial")
    return float(low / 100), float((high - low) / 100)


def headline(
    policy_ref: str | None,
    steps: tuple[str, ...],
    assessments: TrialAssessments,
    withheld: tuple[str, ...] = (),
    declared: Collection[str] | None = None,
) -> Headline:
    """Score one trial under the headline metric (the default by K when none is locked).

    ``steps`` are the trial's planned checkpoints (K = their count).
    ``withheld`` (``withheld_reasons``) keeps the components but suppresses S, C and I.
    """
    checkpoints = len(steps)
    try:
        policy = (
            DEFAULT_METRIC_REGISTRY.get(policy_ref)
            if policy_ref is not None
            else DEFAULT_METRIC_REGISTRY.default_for_checkpoints(checkpoints)
        )
    except RegistryReferenceError as exc:
        ref = policy_ref or "default"
        return Headline(ref, checkpoints, None, None, reasons=(f"{ref}: {exc}",), withheld=withheld)
    correctness, observed, correctness_gap = _correctness(assessments, steps)
    inputs, used = _quality_inputs(policy, assessments)
    inputs[CORRECTNESS] = ComponentInput(value=correctness, gap=correctness_gap)
    if observed is not None:
        used[CORRECTNESS] = observed
    score = score_inputs(policy, checkpoints, inputs)
    reasons = (
        () if score.score is not None else _policy_reasons(policy, score, assessments, declared)
    )
    return Headline(policy.ref, checkpoints, score, correctness, used, reasons, withheld)
