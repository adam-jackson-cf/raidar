"""Assessments projected as the surface's scorecard (``scorer:*`` and ``metric:*`` spans).

The scorecard is the headline metric's view: ``S = 100·C·(base + inertia·I)``.
``scorer:correctness@1`` carries C (the base share) with one metric per
checkpoint, and ``scorer:code-quality@3`` carries I (the inertia share) with one
metric per inertia component at its policy weight; both shares are read from the
policy (``policy_shares``). A scorer area is shown only when its
value is measured; unmeasured components stay visible as ``metric:*`` spans with
their reasons under ``scoring``, never as zero. Scorers the policy does not
weigh (resource usage, process compliance) are observations under
``process metrics``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from raidar.design.experiment.metrics import DEFAULT_METRIC_REGISTRY
from raidar.engine.trial.models import CheckpointState, StepResult, TrialRecord
from raidar.evaluation.analysis.assessed import CODE_QUALITY_REF
from raidar.evaluation.analysis.outcomes import CORRECTNESS_REF
from raidar.evaluation.review.projection.assessments import TrialAssessments
from raidar.evaluation.review.projection.headline import COMPONENT_METRICS, Headline, policy_shares
from raidar.evaluation.review.projection.notes import EvidenceRef, Finding
from raidar.evaluation.review.projection.spans import SpanBuilder
from raidar.scoring.framework.result import AssessStatus, Observation

_EVIDENCE_LIMIT = 10


@dataclass(frozen=True)
class ScoringContext:
    builder: SpanBuilder
    root: str
    start: int
    record: TrialRecord
    headline: Headline
    assessments: TrialAssessments


def _checkpoint_payload(step_result: StepResult, assessments: TrialAssessments) -> dict:
    solved = step_result.checkpoint_state is CheckpointState.SOLVED
    assessment = assessments.get(CORRECTNESS_REF)
    observations = (
        [
            item.model_dump(mode="json")
            for item in assessment.result.observations
            if item.subject == step_result.step
        ]
        if assessment is not None
        else []
    )
    rate = step_result.strict_pass_rate
    return {
        "metric_id": step_result.checkpoint,
        "passed": solved,
        "score": rate,
        "checkpoint_state": step_result.checkpoint_state.value,
        "evidence": (
            f"{step_result.checkpoint_state.value}; strict pass rate "
            + (f"{rate:.3f}" if rate is not None else "unavailable")
        ),
        "observations": observations,
    }


def _correctness(ctx: ScoringContext, scoring: str) -> None:
    step_results = ctx.record.step_results
    area = None
    if ctx.headline.diagnostic is not None:
        base, _ = policy_shares(DEFAULT_METRIC_REGISTRY.get(ctx.headline.policy))
        area = ctx.builder.add(
            name=f"scorer:{CORRECTNESS_REF}",
            span_type="INTERNAL",
            status="OK",
            parent=scoring,
            start=ctx.start,
            output_value={
                "scorer_id": CORRECTNESS_REF,
                "version": 1,
                "weight": base,
                "score": ctx.headline.diagnostic,
                "category": "correctness",
                "component": "C",
                "metric_contributions": [
                    {
                        "metric_id": step_result.checkpoint,
                        "weight": 1 / len(step_results),
                        "score": 1.0
                        if step_result.checkpoint_state is CheckpointState.SOLVED
                        else 0.0,
                    }
                    for step_result in step_results
                ],
            },
        )
    for step_result in step_results:
        if area is None and step_result.checkpoint_state is CheckpointState.NOT_ATTEMPTED:
            continue
        ctx.builder.add(
            name=f"metric:{step_result.checkpoint}",
            span_type="INTERNAL",
            status="OK" if step_result.checkpoint_state is CheckpointState.SOLVED else "ERROR",
            parent=area or scoring,
            start=ctx.start,
            output_value=_checkpoint_payload(step_result, ctx.assessments),
        )


def _component_payload(component: str, observation: Observation | None, weight: Decimal) -> dict:
    measured = observation is not None and observation.status is AssessStatus.OK
    return {
        "metric_id": COMPONENT_METRICS[component],
        "component": component,
        "policy_weight": float(weight),
        "passed": None,
        "status": observation.status.value if observation else "missing",
        "score": observation.value if measured and observation else None,
        "evidence": (
            f"problem-level mean {observation.value:.3f}"
            if measured and observation and observation.value is not None
            else (observation.reason if observation else "no code-quality Assessment")
        ),
    }


def _quality(ctx: ScoringContext, scoring: str) -> None:
    policy = DEFAULT_METRIC_REGISTRY.get(ctx.headline.policy)
    weights = policy.inertia_weights
    quality = CODE_QUALITY_REF
    area = None
    if ctx.headline.quality is not None:
        _, inertia = policy_shares(policy)
        area = ctx.builder.add(
            name=f"scorer:{quality}",
            span_type="INTERNAL",
            status="OK",
            parent=scoring,
            start=ctx.start,
            output_value={
                "scorer_id": quality,
                "version": int(quality.rsplit("@", 1)[1]),
                "weight": inertia,
                "score": ctx.headline.quality,
                "category": "quality",
                "component": "I",
                "metric_contributions": [
                    {
                        "metric_id": COMPONENT_METRICS[component],
                        "weight": float(weight),
                        "score": ctx.headline.components[component].value,
                    }
                    for component, weight in weights.items()
                ],
            },
        )
    for component, weight in weights.items():
        observation = ctx.headline.components.get(component)
        measured = observation is not None and observation.status is AssessStatus.OK
        ctx.builder.add(
            name=f"metric:{COMPONENT_METRICS[component]}",
            span_type="INTERNAL",
            status="OK" if measured else "ERROR",
            parent=area or scoring,
            start=ctx.start,
            output_value=_component_payload(component, observation, weight),
        )


def _scoring_summary(ctx: ScoringContext) -> dict[str, object]:
    score = ctx.headline.score
    return {
        "headline_policy": ctx.headline.policy,
        "checkpoints": ctx.headline.checkpoints,
        "composite_score": ctx.headline.composite,
        "S": str(score.score) if score and score.score is not None else None,
        "C": ctx.headline.diagnostic,
        "I": ctx.headline.quality,
        "components": (
            {key: status.value for key, status in score.components.items()} if score else {}
        ),
        "withheld": list(ctx.headline.withheld),
        "ineligible_reasons": list(ctx.headline.reasons),
        "assessments": {ref: item.key for ref, item in ctx.assessments.found.items()},
        "not_assessed": dict(ctx.assessments.missing),
    }


def _evidence_findings(ctx: ScoringContext) -> list[Finding]:
    quality = CODE_QUALITY_REF
    assessment = ctx.assessments.get(quality)
    if assessment is None:
        return []
    by_metric: dict[str, list[EvidenceRef]] = {}
    for item in assessment.result.evidence:
        refs = by_metric.setdefault(item.metric, [])
        refs.extend(
            EvidenceRef(f"{quality} {item.subject}", location, item.message or item.kind)
            for location in item.locations()
        )
    components = {metric: key for key, metric in COMPONENT_METRICS.items()}
    return [
        Finding(
            id=f"quality-{metric}",
            kind="note",
            category="quality-evidence",
            title=f"{metric.replace('_', ' ')}: {len(refs)} located finding(s)",
            detail="file:line evidence behind this component; the Quality view has the full list",
            span=f"metric:problem_{metric}" if f"problem_{metric}" in components else "scoring",
            evidence=tuple(refs[:_EVIDENCE_LIMIT]),
        )
        for metric, refs in sorted(by_metric.items())
    ]


def _regression_findings(ctx: ScoringContext) -> list[Finding]:
    """Checkpoints where cases carried over from earlier checkpoints fail (correctness@1)."""
    assessment = ctx.assessments.get(CORRECTNESS_REF)
    if assessment is None:
        return []
    return [
        Finding(
            id=f"regression-{item.subject}",
            kind="issue",
            category="regressed-checkpoint",
            title=f"Earlier behaviour broke at {item.subject}",
            detail=f"{int(item.value or 0)} regression case(s) from earlier checkpoints fail",
            span=f"metric:{item.subject}",
            evidence=(EvidenceRef(CORRECTNESS_REF, f"regression_failures[{item.subject}]"),),
        )
        for item in assessment.result.observations
        if item.metric == "regression_failures"
        and item.status is AssessStatus.OK
        and (item.value or 0) > 0
    ]


def _unscored_finding(ctx: ScoringContext) -> list[Finding]:
    if ctx.headline.composite is not None:
        return []
    return [
        Finding(
            id="unscored",
            kind="issue",
            category="unscored-run",
            title=f"No {ctx.headline.policy} headline score",
            detail="; ".join((*ctx.headline.withheld, *ctx.headline.reasons)),
            span="scoring",
            evidence=tuple(
                EvidenceRef("assessment", ref, reason)
                for ref, reason in sorted(ctx.assessments.missing.items())
            ),
        )
    ]


def project_scoring(ctx: ScoringContext) -> list[Finding]:
    """Add the scoring spans and return the findings they support."""
    scoring = ctx.builder.add(
        name="scoring",
        span_type="INTERNAL",
        status="OK" if ctx.headline.composite is not None else "ERROR",
        parent=ctx.root,
        start=ctx.start,
        output_value=_scoring_summary(ctx),
    )
    _correctness(ctx, scoring)
    if ctx.headline.score is not None:
        _quality(ctx, scoring)
    return [*_unscored_finding(ctx), *_regression_findings(ctx), *_evidence_findings(ctx)]
