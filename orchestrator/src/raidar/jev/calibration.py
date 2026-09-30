"""Label import and the enforce-promotion readiness check (plan H2, K4).

Labels come from review-surface annotations in the vocabulary
``correct | false_positive | false_negative | uncertain`` and refer to an
audited decision by its request id. A decision takes one label, as in
``jev-lab``: the first label written stands, and an import that would label a
decision again (or twice in one batch) is refused whole.

Readiness follows ``evaluate-jev/references/policies.md``: at least 30
labelled decisions, at least 5 in the minority reference class, and input
sufficiency of at least 80%. Before calibration there is no frozen threshold,
so a decision's provisional prediction is "positive" when any noul answer is
at least ``PROVISIONAL_THRESHOLD``. The reference class then follows from the
label: a correct positive or a false negative is a reference positive, a
correct negative or a false positive is a reference negative; ``uncertain``
labels are excluded from both.

``verify_promotion`` holds an ``enforce`` policy's recorded promotion to the
audit: the labelled decisions must all come from trials on one side of the
promotion's split, and the audit over those trials must meet the gate and
support every number the policy file claims.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from raidar.jev.audit import (
    AuditLog,
    DecisionRecord,
    DecisionStage,
    LabelRecord,
    SufficiencyLevel,
    utc_timestamp,
)
from raidar.jev.policies import (
    LABELS,
    MIN_INPUT_SUFFICIENCY,
    MIN_LABELLED,
    MIN_MINORITY,
    JevPolicy,
)
from raidar.jev.runtime import RULE_INPUT_MISSING

PROVISIONAL_THRESHOLD = 0.5
_ASKED_STAGES = frozenset({DecisionStage.JEV, DecisionStage.JEV_ERROR})


class LabelImportError(ValueError):
    """An exported label is malformed or names no audited decision of the policy."""


def import_labels(audit: AuditLog, policy: str, rows: Iterable[Mapping[str, str]]) -> int:
    """Append exported labels (``request_id``, ``label``, optional ``note``) for ``policy``."""
    known = {record.request_id for record in audit.decisions(policy)}
    labelled = set(audit.labels(policy))
    pending = []
    for index, row in enumerate(rows):
        request_id, label = row.get("request_id"), row.get("label")
        if label not in LABELS:
            raise LabelImportError(f"row {index}: label {label!r} is not one of {LABELS}")
        if request_id not in known:
            raise LabelImportError(f"row {index}: no {policy} decision {request_id!r}")
        if request_id in labelled:
            raise LabelImportError(f"row {index}: decision {request_id} already has a label")
        labelled.add(request_id)
        pending.append(
            LabelRecord(
                timestamp=utc_timestamp(),
                request_id=request_id,
                policy=policy,
                label=label,
                note=row.get("note") or None,
            )
        )
    for record in pending:
        audit.append_label(record)
    return len(pending)


@dataclass(frozen=True)
class Readiness:
    policy: str
    labelled: int
    reference_positive: int
    reference_negative: int
    asked: int
    sufficient: int

    @property
    def minority(self) -> int:
        return min(self.reference_positive, self.reference_negative)

    @property
    def input_sufficiency(self) -> float | None:
        return self.sufficient / self.asked if self.asked else None

    @property
    def failures(self) -> tuple[str, ...]:
        failures = []
        if self.labelled < MIN_LABELLED:
            failures.append(f"{self.labelled} labelled decisions < {MIN_LABELLED}")
        if self.minority < MIN_MINORITY:
            failures.append(f"{self.minority} minority labels < {MIN_MINORITY}")
        rate = self.input_sufficiency
        if rate is None or rate < MIN_INPUT_SUFFICIENCY:
            failures.append(
                f"input sufficiency {self.sufficient}/{self.asked} < {MIN_INPUT_SUFFICIENCY}"
            )
        return tuple(failures)

    @property
    def ready(self) -> bool:
        return not self.failures


def _provisional_positive(record: DecisionRecord) -> bool:
    answers = record.answers or {}
    return any(
        answer.get("type") == "noul" and answer.get("noul", 0.0) >= PROVISIONAL_THRESHOLD
        for answer in answers.values()
    )


def _reference_positive(record: DecisionRecord, label: str) -> bool | None:
    if label == "uncertain" or record.stage is not DecisionStage.JEV:
        return None
    predicted = _provisional_positive(record)
    return predicted if label == "correct" else not predicted


def _was_asked(record: DecisionRecord) -> bool:
    return record.stage in _ASKED_STAGES or record.rule == RULE_INPUT_MISSING


def _sufficient(record: DecisionRecord) -> bool:
    sufficiency = record.input_sufficiency
    return sufficiency is not None and sufficiency.level is SufficiencyLevel.SUFFICIENT


def _in(record: DecisionRecord, trials: Collection[str] | None) -> bool:
    return trials is None or (record.trial is not None and record.trial in trials)


def promotion_readiness(
    audit: AuditLog, policy: str, *, trials: Collection[str] | None = None
) -> Readiness:
    """Readiness over the policy's audit, or over the decisions of ``trials`` only."""
    decisions = [record for record in audit.decisions(policy) if _in(record, trials)]
    labels = {request_id: record.label for request_id, record in audit.labels(policy).items()}
    positives = negatives = 0
    for record in decisions:
        label = labels.get(record.request_id)
        reference = None if label is None else _reference_positive(record, label)
        positives += reference is True
        negatives += reference is False
    asked = [record for record in decisions if _was_asked(record)]
    return Readiness(
        policy=policy,
        labelled=positives + negatives,
        reference_positive=positives,
        reference_negative=negatives,
        asked=len(asked),
        sufficient=sum(_sufficient(record) for record in asked),
    )


def verify_promotion(policy: JevPolicy, audit: AuditLog) -> tuple[str, ...]:
    """Why the audit does not support ``policy``'s recorded promotion (empty when it does)."""
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
    measured = promotion_readiness(audit, policy.id, trials={*split.calibration, *split.evaluation})
    failures += measured.failures
    claims = (
        ("labelled", promotion.labelled, measured.labelled),
        ("minority", promotion.minority, measured.minority),
        ("input_sufficiency", promotion.input_sufficiency, measured.input_sufficiency or 0.0),
    )
    failures += [
        f"recorded {name} {claimed} exceeds the audited {actual}"
        for name, claimed, actual in claims
        if claimed > actual
    ]
    return tuple(failures)
