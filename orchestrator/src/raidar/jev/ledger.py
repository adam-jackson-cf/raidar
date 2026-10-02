"""The calibration ledger: label supersessions and recorded splits beside a policy's audit.

Nothing in the audit is ever deleted. A label that should no longer count for
calibration (for example because its labeller also chose which decisions to
label, so the blindness of that choice cannot be shown) is *superseded*: a
``LabelSupersession`` naming the label (request id and label timestamp) and
why is appended to the ledger. ``current_labels`` then skips that label, so the
decision counts as unlabelled again and may take a new label, while
``AuditLog.labels``/``label_history`` keep every label ever written. A
calibration/evaluation split chosen before the labels it will judge is recorded
the same way as a ``SplitRecord``; the latest one is the policy's recorded
split, which ``jev promote`` uses when no split is given.

The ledger lives in ``<store>/jev-audit/<policy>/ledger/`` (mode 0600 files,
``jev.ts`` camelCase JSON lines). It is a subdirectory so the audit's decision
files (``<policy>/*.jsonl``) and the Jev runtime that writes them are
untouched: the runtime, and so every scorer implementation digest, never
depends on calibration bookkeeping. Calibration code (label import, blind
export, readiness, promotion, label export, the review surface) reads labels
through ``current_labels``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from raidar.jev.audit import AuditLog, LabelRecord, _append_line, _line, _read, _Record

LEDGER_DIR = "ledger"
SUPERSESSIONS_FILE = "label-supersessions.jsonl"
SPLITS_FILE = "calibration-splits.jsonl"


class LabelSupersession(_Record):
    """Takes one label (by request id and its timestamp) out of calibration, keeping it on file."""

    schema_: Literal[1] = Field(default=1, alias="schema")
    type: Literal["label_supersession"] = "label_supersession"
    timestamp: str
    request_id: str
    policy: str
    label_timestamp: str
    reason: str = Field(min_length=1)


class SplitRecord(_Record):
    """A calibration/evaluation split of trials, fixed before the labels it will judge."""

    schema_: Literal[1] = Field(default=1, alias="schema")
    type: Literal["calibration_split"] = "calibration_split"
    timestamp: str
    policy: str
    split_id: str = Field(min_length=1)
    calibration: tuple[str, ...] = Field(min_length=1)
    evaluation: tuple[str, ...] = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        shared = sorted(set(self.calibration) & set(self.evaluation))
        if shared:
            raise ValueError(f"calibration and evaluation share trials {shared}")
        return self


def _path(audit: AuditLog, policy: str, name: str) -> Path:
    return audit.policy_dir(policy) / LEDGER_DIR / name


def supersessions(audit: AuditLog, policy: str) -> list[LabelSupersession]:
    return _read(_path(audit, policy, SUPERSESSIONS_FILE), LabelSupersession)


def append_supersession(audit: AuditLog, record: LabelSupersession) -> LabelSupersession:
    _append_line(_path(audit, record.policy, SUPERSESSIONS_FILE), _line(record))
    return record


def current_labels(audit: AuditLog, policy: str) -> dict[str, LabelRecord]:
    """The label of each labelled decision: the first one written and not superseded."""
    superseded = {(s.request_id, s.label_timestamp) for s in supersessions(audit, policy)}
    first: dict[str, LabelRecord] = {}
    for record in label_history(audit, policy):
        if (record.request_id, record.timestamp) not in superseded:
            first.setdefault(record.request_id, record)
    return first


def label_history(audit: AuditLog, policy: str) -> list[LabelRecord]:
    """Every label written for ``policy``, superseded ones included, in write order."""
    return _read(audit.labels_path(policy), LabelRecord)


def superseded_request_ids(audit: AuditLog, policy: str) -> set[str]:
    """Decisions that ever had a label superseded."""
    return {record.request_id for record in supersessions(audit, policy)}


def splits(audit: AuditLog, policy: str) -> list[SplitRecord]:
    return _read(_path(audit, policy, SPLITS_FILE), SplitRecord)


def append_split(audit: AuditLog, record: SplitRecord) -> SplitRecord:
    _append_line(_path(audit, record.policy, SPLITS_FILE), _line(record))
    return record


def recorded_split(audit: AuditLog, policy: str) -> SplitRecord | None:
    """The latest recorded split of ``policy``, if any."""
    recorded = splits(audit, policy)
    return recorded[-1] if recorded else None
