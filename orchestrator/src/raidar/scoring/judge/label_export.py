"""Export review-surface Jev labels as calibration rows for ``import_labels`` (plan K4).

Reviewers label a run's Jev decisions on the review surface's Quality page;
the server keeps those labels in ``review-surface/data/jev-labels.json``.
This module turns them into the JSON-lines rows ``raidar jev import-labels``
reads (``request_id``, ``label``, ``reviewer``, optional ``reviewer_model``
and ``note``), one policy per file.

Rules, matching the import side (``calibration.import_labels``):

- the vocabulary is ``correct | false_positive | false_negative | uncertain``;
- each row names its reviewer: ``reviewer`` (``human`` | ``model``, with
  ``reviewer_model`` for a model) when the surface recorded one, else the
  label's ``source``, where the surface's ``user`` is ``human``;
- one row per decision: when a decision was labelled more than once on the
  surface, the latest label wins;
- with the store's audit, decisions it does not know are refused, and
  decisions it already labelled are left out (and counted), because the
  import refuses a batch that would label a decision again.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from raidar.scoring.judge.audit import AuditLog, ReviewerKind, reviewer_kind
from raidar.scoring.judge.ledger import current_labels
from raidar.scoring.judge.policies import LABELS


class LabelExportError(ValueError):
    """The review labels are malformed or name decisions the audit does not hold."""


class ReviewLabel(BaseModel):
    """One label saved by the review surface (extra server fields are ignored)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    job_id: str
    request_id: str
    policy: str
    label: str
    note: str | None = None
    created_at: int = 0
    source: str | None = None
    reviewer: str | None = None
    reviewer_model: str | None = None

    @model_validator(mode="after")
    def _known_reviewer(self) -> ReviewLabel:
        kind = self.reviewer_kind
        if kind is ReviewerKind.MODEL and not (self.reviewer_model or "").strip():
            raise ValueError("a model reviewer's label needs reviewer_model")
        return self

    @property
    def reviewer_kind(self) -> ReviewerKind:
        return reviewer_kind(self.reviewer or self.source)

    @field_validator("label")
    @classmethod
    def _known_label(cls, value: str) -> str:
        if value not in LABELS:
            raise ValueError(f"label {value!r} is not one of {LABELS}")
        return value


@dataclass(frozen=True)
class LabelExport:
    policy: str
    rows: tuple[dict[str, str], ...]
    superseded: int
    already_labelled: tuple[str, ...]

    def summary(self) -> dict[str, object]:
        return {
            "policy": self.policy,
            "exported": len(self.rows),
            "superseded_on_surface": self.superseded,
            "already_labelled": list(self.already_labelled),
            "by_reviewer": self.by_reviewer(),
        }

    def by_reviewer(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row["reviewer"]] = counts.get(row["reviewer"], 0) + 1
        return dict(sorted(counts.items()))


def load_review_labels(path: Path) -> list[ReviewLabel]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LabelExportError(f"cannot read review labels {path}: {error}") from error
    if not isinstance(payload, list):
        raise LabelExportError(f"{path} must hold a JSON list of labels")
    try:
        return [ReviewLabel.model_validate(item) for item in payload]
    except ValidationError as error:
        raise LabelExportError(f"{path}: {error}") from error


def _latest(labels: Iterable[ReviewLabel], policy: str) -> tuple[list[ReviewLabel], int]:
    chosen: dict[str, ReviewLabel] = {}
    total = 0
    for label in labels:
        if label.policy != policy:
            continue
        total += 1
        current = chosen.get(label.request_id)
        if current is None or label.created_at >= current.created_at:
            chosen[label.request_id] = label
    return sorted(chosen.values(), key=lambda item: item.request_id), total - len(chosen)


def _against_audit(
    chosen: Sequence[ReviewLabel], policy: str, audit: AuditLog
) -> tuple[list[ReviewLabel], list[str]]:
    """Refuse unknown decisions; leave out (and name) ones the audit already labelled."""
    known = {record.request_id for record in audit.decisions(policy)}
    unknown = sorted(label.request_id for label in chosen if label.request_id not in known)
    if unknown:
        raise LabelExportError(f"the audit holds no {policy} decision for {unknown}")
    labelled = set(current_labels(audit, policy))
    already = [label.request_id for label in chosen if label.request_id in labelled]
    return [label for label in chosen if label.request_id not in labelled], already


def _row(label: ReviewLabel) -> dict[str, str]:
    kind = label.reviewer_kind
    row = {"request_id": label.request_id, "label": label.label, "reviewer": kind.value}
    if kind is ReviewerKind.MODEL and label.reviewer_model:
        row["reviewer_model"] = label.reviewer_model
    if label.note:
        row["note"] = label.note
    return row


def export_labels(
    labels: Sequence[ReviewLabel], policy: str, *, audit: AuditLog | None = None
) -> LabelExport:
    """Calibration rows for ``policy``, checked against ``audit`` when given."""
    chosen, superseded = _latest(labels, policy)
    already: list[str] = []
    if audit is not None:
        chosen, already = _against_audit(chosen, policy, audit)
    return LabelExport(
        policy=policy,
        rows=tuple(_row(label) for label in chosen),
        superseded=superseded,
        already_labelled=tuple(already),
    )


def write_rows(export: LabelExport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in export.rows), encoding="utf-8"
    )
