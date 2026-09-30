"""The experiment's headline ScorePolicy applied to one trajectory's Assessments.

``composite_score`` is S/100 under the headline policy (``scb-canonical@1`` or
its one-shot variant), so the review surface's delivery tiers keep their
meaning: S is correctness-gated. ``quality_score`` is the policy's inertia I and
``diagnostic_score`` is correctness C. C comes from ``correctness@1``'s
``solved_checkpoints``; V/E/H/R/G come from ``code-quality@1``'s problem-level
means. A component that is not ``ok`` makes the score ineligible with the
scorer's own reason, and a scorer with no stored Assessment makes it
unassessed; neither is ever read as zero.

Two trajectory states withhold the headline even when every component is
measured, exactly as ``raidar analyze`` excludes them (``analysis.assessed``):
an infra-invalid execution, and a ``max_checkpoints`` truncation, whose K is
not the problem's K. Their components stay visible; S, C and I do not.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from raidar.analysis.assessed import infra_reasons
from raidar.execution.models import CheckpointState, TrajectoryRecord
from raidar.review_projection.assessments import TrialAssessments
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessStatus, Observation
from raidar.spec.policies import (
    CORRECTNESS,
    DEFAULT_POLICY_REGISTRY,
    ComponentValue,
    PolicyScore,
    ScorePolicy,
    checkpoint_correctness,
)
from raidar.spec.references import RegistryReferenceError

CORRECTNESS_SCORER = "correctness@1"
QUALITY_SCORER = "code-quality@1"
SOLVED_METRIC = "solved_checkpoints"
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
    score: PolicyScore | None
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


def withheld_reasons(record: TrajectoryRecord) -> tuple[str, ...]:
    """Why this trajectory has no headline even with every component measured."""
    reasons = [
        f"Execution invalid: {check.name}: {check.detail or 'failed'}"
        for check in record.validity.checks
        if not check.passed
    ]
    reasons += [
        f"Execution invalid: {attempt.step} infra error: "
        + (", ".join(attempt.infra_reasons) or "infra_error")
        for attempt in record.attempts
        if attempt.checkpoint_state is CheckpointState.INFRA_ERROR
    ]
    if not reasons and (infra_reasons(record) or not record.valid):
        reasons.append("Execution invalid: the trial raised an exception")
    if record.truncated_to is not None:
        reasons.append(
            f"Truncated run: only the first {record.truncated_to} checkpoint(s) ran "
            "(max_checkpoints); the problem-level score needs every checkpoint"
        )
    return tuple(reasons)


def trajectory_observation(
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
    assessments: TrialAssessments, checkpoints: int
) -> tuple[Decimal | None, Observation | None, str | None]:
    observation = trajectory_observation(assessments, CORRECTNESS_SCORER, SOLVED_METRIC)
    if observation is None or observation.status is not AssessStatus.OK:
        reason = _unmeasured(observation, assessments.missing.get(CORRECTNESS_SCORER))
        return None, observation, reason
    solved = int(observation.value or 0)
    return checkpoint_correctness(solved, checkpoints), observation, None


def _quality_inputs(
    policy: ScorePolicy, assessments: TrialAssessments
) -> tuple[dict[str, ComponentValue], dict[str, list[str]], dict[str, Observation]]:
    values: dict[str, ComponentValue] = {}
    ineligible: dict[str, list[str]] = {}
    used: dict[str, Observation] = {}
    for component in policy.inertia_weights:
        observation = trajectory_observation(
            assessments, QUALITY_SCORER, COMPONENT_METRICS[component]
        )
        if observation is not None:
            used[component] = observation
        if observation is not None and observation.status is AssessStatus.OK:
            values[component] = float(observation.value or 0.0)
        else:
            missing = assessments.missing.get(QUALITY_SCORER)
            ineligible[component] = [_unmeasured(observation, missing)]
    return values, ineligible, used


def _reason_text(policy: str, component: str | None, detail: str) -> str:
    label = f"{component} ({COMPONENT_NAMES[component]})" if component else "policy"
    return f"{policy} ineligible: {label}: {detail}"


def _policy_reasons(
    policy: ScorePolicy, score: PolicyScore, assessments: TrialAssessments
) -> tuple[str, ...]:
    """Unassessed takes precedence over ineligible: "not scored yet" is not "not scorable"."""
    unassessed = [ref for ref in (CORRECTNESS_SCORER, QUALITY_SCORER) if ref in assessments.missing]
    if unassessed:
        return tuple(
            f"{policy.ref} unassessed: {ref}: {assessments.missing[ref]}" for ref in unassessed
        )
    return tuple(
        _reason_text(policy.ref, reason.component, reason.detail) for reason in score.reasons
    )


def policy_shares(policy: ScorePolicy) -> tuple[float, float]:
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
        raise ValueError(f"{policy.ref} does not score a fully measured trajectory")
    return float(low / 100), float((high - low) / 100)


def headline(
    policy_ref: str | None,
    checkpoints: int,
    assessments: TrialAssessments,
    withheld: tuple[str, ...] = (),
) -> Headline:
    """Score one trajectory under the headline policy (the default by K when none is locked).

    ``withheld`` (``withheld_reasons``) keeps the components but suppresses S, C and I.
    """
    try:
        policy = (
            DEFAULT_POLICY_REGISTRY.get(policy_ref)
            if policy_ref is not None
            else DEFAULT_POLICY_REGISTRY.default_for_checkpoints(checkpoints)
        )
    except RegistryReferenceError as exc:
        ref = policy_ref or "default"
        return Headline(ref, checkpoints, None, None, reasons=(f"{ref}: {exc}",), withheld=withheld)
    correctness, solved, correctness_gap = _correctness(assessments, checkpoints)
    values, ineligible, used = _quality_inputs(policy, assessments)
    if correctness is not None:
        values[CORRECTNESS] = correctness
    elif correctness_gap is not None:
        ineligible[CORRECTNESS] = [correctness_gap]
    if solved is not None:
        used[CORRECTNESS] = solved
    score = policy.score(checkpoints=checkpoints, components=values, ineligible=ineligible)
    reasons = _policy_reasons(policy, score, assessments)
    return Headline(policy.ref, checkpoints, score, correctness, used, reasons, withheld)
