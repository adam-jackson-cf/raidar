"""Fit, record and verify a policy's promotion to ``enforce`` (plan H2, K4).

A promotion is built from the store's audit and labels, never typed by hand:

1. **Split.** Trials go to the calibration or the evaluation side, never both
   (a trial is one trajectory, so its near-identical checkpoints stay on one
   side). An explicit split is used as given; otherwise ``default_split``
   orders the labelled trials by the sha256 of their id and puts the first
   half (rounded up) on the calibration side, so the split is reproducible
   and independent of labelling order.
2. **Threshold.** On the calibration side only, each labelled decision is a
   point (score, reference class): the score is its highest noul answer and
   the class follows from the label (``calibration.reference_positive``).
   ``fit_threshold`` picks the cut with the highest agreement; ties go to the
   cut nearest the provisional 0.5, then to the lower cut. The threshold is
   frozen in the promotion and applied unchanged to the evaluation side.
3. **Metrics.** Precision, recall and agreement of the frozen threshold, per
   side (``None`` when a denominator is 0).
4. **Regression corpus.** The sha256 of the labelled decisions in the split:
   request id, trial, side, cache key, answers, label and reviewer. Any
   relabel, new label or moved trial changes it.
5. **Reviewers.** Labelled counts per reviewer kind and the model ids of model
   reviewers, so a reader can see whether a promotion rests on human labels.
6. **Scorer.** Promoting a policy changes what its scorer's metrics mean, so
   the promotion names the scorer version it ships with: the next minor
   version of the scorer the policy id belongs to (``code-quality`` for
   ``code-quality.rubric-smells``). ``verify_promotion`` fails until that
   scorer's manifest carries the version.

``verify_promotion`` re-derives every recorded number from the audit and
fails on any difference; ``promote`` writes the promotion block and ``mode:
enforce`` to the policy file only when everything but the scorer bump
verifies, and reports the bump as a required follow-up.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from raidar.jev.audit import AuditLog, DecisionRecord, LabelRecord, ReviewerKind
from raidar.jev.calibration import (
    PROVISIONAL_THRESHOLD,
    Readiness,
    decision_score,
    promotion_readiness,
    reference_positive,
)
from raidar.jev.policies import (
    CalibrationSplit,
    JevPolicy,
    PolicyMode,
    Promotion,
    SplitMetrics,
    load_policy,
)
from raidar.spec.canonical import digest_json

THRESHOLD_DIGITS = 6
METRIC_DIGITS = 4


class PromotionError(ValueError):
    """The audit cannot support a promotion (no split, one class only, gate not met)."""


@dataclass(frozen=True)
class LabelledPoint:
    record: DecisionRecord
    label: LabelRecord
    positive: bool

    @property
    def score(self) -> float:
        return decision_score(self.record)


def labelled_points(
    audit: AuditLog, policy: str, trials: Collection[str] | None = None
) -> list[LabelledPoint]:
    """Reference-classed labelled decisions (of ``trials`` when given), by request id."""
    labels = audit.labels(policy)
    points = []
    for record in audit.decisions(policy):
        label = labels.get(record.request_id)
        if label is None or (trials is not None and record.trial not in trials):
            continue
        reference = reference_positive(record, label.label)
        if reference is not None:
            points.append(LabelledPoint(record, label, reference))
    return sorted(points, key=lambda point: point.record.request_id)


def labelled_trials(audit: AuditLog, policy: str) -> tuple[str, ...]:
    return tuple(sorted({str(p.record.trial) for p in labelled_points(audit, policy)}))


def default_split(trials: Sequence[str]) -> CalibrationSplit:
    """First half (rounded up) of ``trials`` by sha256 order calibrates; the rest evaluates."""
    if len(set(trials)) < 2:
        raise PromotionError(f"a split needs labelled decisions from at least 2 trials: {trials}")
    ordered = sorted(set(trials), key=lambda trial: hashlib.sha256(trial.encode()).hexdigest())
    cut = math.ceil(len(ordered) / 2)
    return CalibrationSplit(calibration=tuple(ordered[:cut]), evaluation=tuple(ordered[cut:]))


def _agreement(points: Sequence[tuple[float, bool]], threshold: float) -> int:
    return sum((score >= threshold) is positive for score, positive in points)


def fit_threshold(points: Sequence[tuple[float, bool]]) -> float:
    """The cut in (0, 1) with the most agreement on ``points`` (score, reference positive)."""
    if not any(positive for _, positive in points) or all(positive for _, positive in points):
        raise PromotionError("the calibration side needs labelled positives and negatives")
    scores = sorted({score for score, _ in points})
    edges = [0.0, *scores, 1.0]
    pairs = zip(edges, edges[1:], strict=False)
    candidates = {round((low + high) / 2, THRESHOLD_DIGITS) for low, high in pairs}
    candidates.add(PROVISIONAL_THRESHOLD)
    usable = sorted(cut for cut in candidates if 0.0 < cut < 1.0)
    return min(
        usable,
        key=lambda cut: (-_agreement(points, cut), abs(cut - PROVISIONAL_THRESHOLD), cut),
    )


def _floor(value: float) -> float:
    scale = 10**METRIC_DIGITS
    return math.floor(value * scale) / scale


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, METRIC_DIGITS) if denominator else None


def split_metrics(points: Sequence[tuple[float, bool]], threshold: float) -> SplitMetrics:
    predicted = [(score >= threshold, positive) for score, positive in points]
    true_pos = sum(guess and truth for guess, truth in predicted)
    false_pos = sum(guess and not truth for guess, truth in predicted)
    false_neg = sum(truth and not guess for guess, truth in predicted)
    return SplitMetrics(
        labelled=len(points),
        precision=_rate(true_pos, true_pos + false_pos),
        recall=_rate(true_pos, true_pos + false_neg),
        agreement=_rate(sum(guess is truth for guess, truth in predicted), len(points)),
    )


def regression_corpus(audit: AuditLog, policy: str, split: CalibrationSplit) -> str:
    """sha256 of every labelled decision (uncertain ones included) in ``split``'s trials."""
    labels = audit.labels(policy)
    entries: list[dict[str, Any]] = []
    for record in audit.decisions(policy):
        label = labels.get(record.request_id)
        side = split.side(record.trial)
        if label is None or side is None:
            continue
        entries.append(
            {
                "request_id": record.request_id,
                "trial": record.trial,
                "side": side,
                "cache_key": record.cache_key,
                "answers": record.answers,
                "label": label.label,
                "reviewer": label.reviewer_ref,
                "blind": bool(label.blind),
            }
        )
    return digest_json(sorted(entries, key=lambda entry: entry["request_id"]))


def next_minor(version: str) -> str:
    """``1`` -> ``1.1``; ``1.2`` -> ``1.3``."""
    parts = version.split(".")
    if len(parts) == 1:
        return f"{version}.1"
    if not parts[1].isdigit():
        raise PromotionError(f"cannot bump the minor version of {version!r}")
    return ".".join([parts[0], str(int(parts[1]) + 1), *parts[2:]])


def scorer_id(policy_id: str) -> str:
    """The scorer a policy belongs to: the first dotted part of its id."""
    return policy_id.split(".", 1)[0]


@dataclass(frozen=True)
class _Derived:
    threshold: float
    calibration: SplitMetrics
    evaluation: SplitMetrics
    readiness: Readiness
    corpus: str
    reviewers: dict[str, int]
    reviewer_models: tuple[str, ...]


def _derive(policy_id: str, audit: AuditLog, split: CalibrationSplit) -> _Derived:
    def points(trials: Collection[str]) -> list[tuple[float, bool]]:
        return [(p.score, p.positive) for p in labelled_points(audit, policy_id, trials)]

    calibration, evaluation = points(split.calibration), points(split.evaluation)
    threshold = fit_threshold(calibration)
    readiness = promotion_readiness(
        audit, policy_id, trials={*split.calibration, *split.evaluation}
    )
    labelled = labelled_points(audit, policy_id, {*split.calibration, *split.evaluation})
    models = {p.label.reviewer_model for p in labelled if p.label.reviewer is ReviewerKind.MODEL}
    return _Derived(
        threshold=threshold,
        calibration=split_metrics(calibration, threshold),
        evaluation=split_metrics(evaluation, threshold),
        readiness=readiness,
        corpus=regression_corpus(audit, policy_id, split),
        reviewers=readiness.reviewer_kinds,
        reviewer_models=tuple(sorted(model for model in models if model)),
    )


def build_promotion(
    policy: JevPolicy,
    audit: AuditLog,
    split: CalibrationSplit,
    *,
    scorer_version: str | None,
) -> Promotion:
    """The promotion the audit supports for ``policy`` over ``split``.

    ``scorer_version`` is the current version of the policy's scorer; the
    promotion names the next minor version, or no scorer when there is none.
    """
    derived = _derive(policy.id, audit, split)
    scorer = None
    if scorer_version is not None:
        scorer = f"{scorer_id(policy.id)}@{next_minor(scorer_version)}"
    return Promotion(
        threshold=derived.threshold,
        labelled=derived.readiness.labelled,
        minority=derived.readiness.minority,
        # Rounded down, so the recorded claim never exceeds the audited rate.
        input_sufficiency=_floor(derived.readiness.input_sufficiency or 0.0),
        runtimes=tuple(runtime for runtime in policy.runtimes if runtime != "*") or ("*",),
        split=split,
        regression_corpus=derived.corpus,
        reviewers=derived.reviewers,  # type: ignore[arg-type]
        reviewer_models=derived.reviewer_models,
        calibration_metrics=derived.calibration,
        evaluation_metrics=derived.evaluation,
        scorer=scorer,
    )


def _claim_failures(promotion: Promotion, audited: Readiness) -> list[str]:
    claims = (
        ("labelled", promotion.labelled, audited.labelled),
        ("minority", promotion.minority, audited.minority),
        ("input_sufficiency", promotion.input_sufficiency, audited.input_sufficiency or 0.0),
    )
    return [
        f"recorded {name} {claimed} exceeds the audited {actual}"
        for name, claimed, actual in claims
        if claimed > actual
    ]


def _rederivation_failures(promotion: Promotion, derived: _Derived) -> list[str]:
    expected: tuple[tuple[str, object, object], ...] = (
        ("threshold", promotion.threshold, derived.threshold),
        ("regression_corpus", promotion.regression_corpus, derived.corpus),
        ("calibration_metrics", promotion.calibration_metrics, derived.calibration),
        ("evaluation_metrics", promotion.evaluation_metrics, derived.evaluation),
        ("reviewers", promotion.reviewers, derived.reviewers),
        ("reviewer_models", promotion.reviewer_models, derived.reviewer_models),
    )
    return [
        f"recorded {name} {recorded} is not the audited {audited}"
        for name, recorded, audited in expected
        if recorded != audited
    ]


def _scorer_failures(promotion: Promotion, scorer_versions: Mapping[str, str]) -> list[str]:
    if promotion.scorer is None:
        return ["the promotion names no scorer version to ship with"]
    name, version = promotion.scorer.split("@", 1)
    current = scorer_versions.get(name)
    if current != version:
        return [
            f"scorer {name} is at {current or 'no version'}; the promotion ships with "
            f"{promotion.scorer}, so bump its manifest (and registrations) first"
        ]
    return []


def verify_promotion(
    policy: JevPolicy,
    audit: AuditLog,
    *,
    scorer_versions: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Why the audit does not support ``policy``'s recorded promotion (empty when it does).

    ``scorer_versions`` (scorer id -> highest registered version) enables the
    scorer-bump check; without it that check is skipped.
    """
    promotion = policy.promotion
    if promotion is None:
        return (f"policy {policy.id} has no recorded promotion",)
    split = promotion.split
    trial_of = {record.request_id: record.trial for record in audit.decisions(policy.id)}
    outside = sorted(
        {
            str(trial_of[request_id])
            for request_id in audit.labels(policy.id)
            if request_id in trial_of and split.side(trial_of[request_id]) is None
        }
    )
    failures = [f"labelled decisions from trials outside the split: {outside}"] if outside else []
    failures += promotion.gate_failures(policy.runtimes)
    try:
        derived = _derive(policy.id, audit, split)
    except PromotionError as error:
        failures.append(str(error))
        audited = promotion_readiness(
            audit, policy.id, trials={*split.calibration, *split.evaluation}
        )
        return (*failures, *audited.failures, *_claim_failures(promotion, audited))
    failures += derived.readiness.failures
    failures += _claim_failures(promotion, derived.readiness)
    failures += _rederivation_failures(promotion, derived)
    if scorer_versions is not None:
        failures += _scorer_failures(promotion, scorer_versions)
    return tuple(dict.fromkeys(failures))


def _bumped(version: str) -> str:
    return str(int(version) + 1) if version.isdigit() else next_minor(version)


@dataclass(frozen=True)
class PromotionPlan:
    policy: JevPolicy
    failures: tuple[str, ...]
    scorer_bump: dict[str, str] | None

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> dict[str, object]:
        promotion = self.policy.promotion
        return {
            "policy": self.policy.id,
            "version": self.policy.version,
            "mode": self.policy.mode.value,
            "promotion": None if promotion is None else promotion.model_dump(mode="json"),
            "verified": self.ok,
            "failures": list(self.failures),
            "scorer_bump_required": self.scorer_bump,
        }


def plan_promotion(
    policy: JevPolicy,
    audit: AuditLog,
    *,
    split: CalibrationSplit | None,
    scorer_version: str | None,
) -> PromotionPlan:
    """The enforce policy the audit supports, or why it does not support one."""
    try:
        chosen = split or default_split(labelled_trials(audit, policy.id))
        promotion = build_promotion(policy, audit, chosen, scorer_version=scorer_version)
    except PromotionError as error:
        readiness = promotion_readiness(audit, policy.id)
        return PromotionPlan(policy, (str(error), *readiness.failures), None)
    try:
        promoted = JevPolicy.model_validate(
            {
                **policy.model_dump(),
                "version": _bumped(policy.version),
                "mode": PolicyMode.ENFORCE,
                "promotion": promotion,
            }
        )
    except ValueError as error:
        shadow = policy.model_copy(update={"promotion": promotion})
        return PromotionPlan(shadow, (str(error),), None)
    failures = verify_promotion(promoted, audit)
    bump = None
    if promotion.scorer is not None and scorer_version is not None:
        bump = {
            "scorer": scorer_id(policy.id),
            "from": scorer_version,
            "to": promotion.scorer.split("@", 1)[1],
        }
    return PromotionPlan(promoted, failures, bump)


def promoted_yaml(path: Path, policy: JevPolicy) -> str:
    """The policy file for ``policy``, keeping the original file's leading comments."""
    header = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            break
        header.append(line)
    header.append(f"# Promoted to enforce at version {policy.version} by `make jev-promote`.")
    body = yaml.safe_dump(
        policy.model_dump(mode="json", exclude_defaults=True), sort_keys=False, width=100
    )
    return "\n".join(header) + "\n" + body


def write_promotion(path: Path, plan: PromotionPlan) -> None:
    """Write ``plan``'s enforce policy to ``path``; refused unless the plan verified."""
    if not plan.ok:
        raise PromotionError(f"promotion of {plan.policy.id} does not verify: {plan.failures}")
    text = promoted_yaml(path, plan.policy)
    path.write_text(text, encoding="utf-8")
    if load_policy(path) != plan.policy:
        raise PromotionError(f"{path} did not round-trip the promoted policy")
