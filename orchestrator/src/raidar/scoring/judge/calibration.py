"""Label import and the enforce-promotion readiness check (plan H2, K4).

Labels come from review-surface annotations (or blind labelling,
``raidar.scoring.judge.blind``) in the vocabulary
``correct | false_positive | false_negative | uncertain`` and refer to an
audited decision by its request id. Each row may name its reviewer
(``human``, the default, or ``model`` with ``reviewer_model``; the surface's
``user`` is ``human``), and readiness reports labelled counts per reviewer.
A decision takes one label, as in ``jev-lab``: the first label written
stands, and an import that would label a decision again (or twice in one batch) is refused whole.

Readiness follows ``evaluate-jev/references/policies.md``: at least 30
labelled decisions, at least 5 in the minority reference class, and input
sufficiency of at least 80%. Before calibration there is no frozen threshold,
so a decision's provisional prediction is "positive" when any noul answer is
at least ``PROVISIONAL_THRESHOLD``. The reference class then follows from the
label: a correct positive or a false negative is a reference positive, a
correct negative or a false positive is a reference negative; ``uncertain``
labels are excluded from both.

A label is never deleted: ``supersede_labels`` takes current labels out of
calibration (``ledger.LabelSupersession``) so their decisions can be labelled
again, and ``record_split`` fixes a calibration/evaluation split of the
audited trials before the labels it will judge are written.

Fitting the frozen threshold, building a promotion and verifying a recorded
one against the audit live in ``raidar.scoring.judge.promotion``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from raidar.scoring.judge.audit import (
    AuditLog,
    DecisionRecord,
    DecisionStage,
    LabelRecord,
    ReviewerKind,
    SufficiencyLevel,
    reviewer_kind,
    utc_timestamp,
)
from raidar.scoring.judge.ledger import (
    LabelSupersession,
    SplitRecord,
    append_split,
    append_supersession,
    current_labels,
    splits,
)
from raidar.scoring.judge.policies import (
    LABELS,
    MIN_INPUT_SUFFICIENCY,
    MIN_LABELLED,
    MIN_MINORITY,
)
from raidar.scoring.judge.runtime import RULE_INPUT_MISSING

PROVISIONAL_THRESHOLD = 0.5
_ASKED_STAGES = frozenset({DecisionStage.JEV, DecisionStage.JEV_ERROR})


class LabelImportError(ValueError):
    """An exported label is malformed or names no audited decision of the policy."""


def _label_record(index: int, policy: str, row: Mapping[str, object]) -> LabelRecord:
    try:
        kind = reviewer_kind(row.get("reviewer"))
        model = row.get("reviewer_model")
        return LabelRecord(
            timestamp=utc_timestamp(),
            request_id=str(row["request_id"]),
            policy=policy,
            label=str(row["label"]),
            reviewer=kind,
            reviewer_model=str(model) if model and kind is ReviewerKind.MODEL else None,
            blind=True if row.get("blind") else None,
            note=str(row.get("note") or "") or None,
        )
    except ValueError as error:
        raise LabelImportError(f"row {index}: {error}") from error


def import_labels(audit: AuditLog, policy: str, rows: Iterable[Mapping[str, object]]) -> int:
    """Append exported labels for ``policy``.

    A row holds ``request_id``, ``label``, and optionally ``note``,
    ``reviewer`` (``human`` | ``model``; ``user`` means ``human``),
    ``reviewer_model`` (required for ``model``) and ``blind``.
    """
    known = {record.request_id for record in audit.decisions(policy)}
    labelled = set(current_labels(audit, policy))
    pending = []
    for index, row in enumerate(rows):
        request_id, label = row.get("request_id"), row.get("label")
        if label not in LABELS:
            raise LabelImportError(f"row {index}: label {label!r} is not one of {LABELS}")
        if request_id not in known:
            raise LabelImportError(f"row {index}: no {policy} decision {request_id!r}")
        if request_id in labelled:
            raise LabelImportError(f"row {index}: decision {request_id} already has a label")
        labelled.add(str(request_id))
        pending.append(_label_record(index, policy, row))
    for record in pending:
        audit.append_label(record)
    return len(pending)


def supersede_labels(
    audit: AuditLog,
    policy: str,
    *,
    reason: str,
    request_ids: Collection[str] | None = None,
) -> list[LabelSupersession]:
    """Supersede the current labels of ``request_ids`` (all current labels when None).

    Refused whole when a named decision has no current label or the reason is blank.
    """
    if not reason.strip():
        raise LabelImportError("a supersession needs a reason")
    current = current_labels(audit, policy)
    chosen = sorted(current) if request_ids is None else sorted(set(request_ids))
    missing = [request_id for request_id in chosen if request_id not in current]
    if missing:
        raise LabelImportError(f"no current {policy} label for {missing}")
    if not chosen:
        raise LabelImportError(f"{policy} has no current label to supersede")
    timestamp = utc_timestamp()
    records = [
        LabelSupersession(
            timestamp=timestamp,
            request_id=request_id,
            policy=policy,
            label_timestamp=current[request_id].timestamp,
            reason=reason,
        )
        for request_id in chosen
    ]
    for record in records:
        append_supersession(audit, record)
    return records


def record_split(
    audit: AuditLog,
    policy: str,
    *,
    split_id: str,
    calibration: Collection[str],
    evaluation: Collection[str],
    reason: str,
) -> SplitRecord:
    """Append a split of audited trials; refused when it names a trial the audit lacks
    or reuses an id already recorded."""
    known = {record.trial for record in audit.decisions(policy) if record.trial is not None}
    unknown = sorted((set(calibration) | set(evaluation)) - known)
    if unknown:
        raise LabelImportError(f"no {policy} decisions for trials {unknown}")
    if any(split.split_id == split_id for split in splits(audit, policy)):
        raise LabelImportError(f"split {split_id!r} is already recorded for {policy}")
    try:
        record = SplitRecord(
            timestamp=utc_timestamp(),
            policy=policy,
            split_id=split_id,
            calibration=tuple(sorted(set(calibration))),
            evaluation=tuple(sorted(set(evaluation))),
            reason=reason,
        )
    except ValueError as error:
        raise LabelImportError(str(error)) from error
    return append_split(audit, record)


@dataclass(frozen=True)
class Readiness:
    policy: str
    labelled: int
    reference_positive: int
    reference_negative: int
    asked: int
    sufficient: int
    by_reviewer: Mapping[str, int] = field(default_factory=dict)
    """Reference-classed labels per reviewer: ``human`` and ``model:<model id>``."""

    @property
    def reviewer_kinds(self) -> dict[Literal["human", "model"], int]:
        """Reference-classed labels per reviewer kind (``human`` | ``model``)."""
        humans = sum(c for ref, c in self.by_reviewer.items() if ref == ReviewerKind.HUMAN)
        models = sum(c for ref, c in self.by_reviewer.items() if ref != ReviewerKind.HUMAN)
        kinds: dict[Literal["human", "model"], int] = {}
        if humans:
            kinds["human"] = humans
        if models:
            kinds["model"] = models
        return kinds

    def summary(self) -> dict[str, object]:
        return {
            "labelled": self.labelled,
            "minority": self.minority,
            "reference_positive": self.reference_positive,
            "reference_negative": self.reference_negative,
            "input_sufficiency": self.input_sufficiency,
            "labelled_by_reviewer_kind": self.reviewer_kinds,
            "labelled_by_reviewer": dict(sorted(self.by_reviewer.items())),
            "ready_for_enforce": self.ready,
            "gate_failures": list(self.failures),
        }

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


def decision_score(record: DecisionRecord) -> float:
    """The decision's highest noul answer (0 when it has none): what a threshold cuts."""
    answers = record.answers or {}
    values = [
        float(answer.get("noul", 0.0))
        for answer in answers.values()
        if isinstance(answer, Mapping) and answer.get("type") == "noul"
    ]
    return max(values, default=0.0)


def provisional_positive(record: DecisionRecord) -> bool:
    """Jev's prediction before calibration: some noul answer is at least the provisional cut."""
    return decision_score(record) >= PROVISIONAL_THRESHOLD


def reference_positive(record: DecisionRecord, label: str) -> bool | None:
    """The labelled truth: positive, negative, or None (uncertain or not a Jev answer)."""
    if label == "uncertain" or record.stage is not DecisionStage.JEV:
        return None
    predicted = provisional_positive(record)
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
    labels = current_labels(audit, policy)
    positives = negatives = 0
    reviewers: Counter[str] = Counter()
    for record in decisions:
        label = labels.get(record.request_id)
        reference = None if label is None else reference_positive(record, label.label)
        positives += reference is True
        negatives += reference is False
        if label is not None and reference is not None:
            reviewers[label.reviewer_ref] += 1
    asked = [record for record in decisions if _was_asked(record)]
    return Readiness(
        policy=policy,
        labelled=positives + negatives,
        reference_positive=positives,
        reference_negative=negatives,
        asked=len(asked),
        sufficient=sum(_sufficient(record) for record in asked),
        by_reviewer=dict(reviewers),
    )
