"""Jev decision audit, stored with the run's assessment artifacts (plan H2).

Records keep the ``DecisionRecord``/``LabelRecord`` shape of ``jev.ts``
(``appendDecision``/``appendLabel``, camelCase JSON), so the Jev lab tooling
can read them, plus the fields Raidar needs: the question set, the cache key
and whether the decision was a cache hit, the input sufficiency of the state,
and ``countsTowardVerdicts``, which is true only for a successful ``enforce``
decision. Records are redacted before they are written.

The layout is the ``jev.ts`` one, rooted beside the Assessment store instead
of ``~/.omp``: decisions append to ``<store>/jev-audit/<policy>/<YYYY-MM-DD>.jsonl``
(UTC date of the record) and labels to ``<store>/jev-audit/<policy>/labels.jsonl``,
mode 0600. Absent optional fields are omitted, never written as ``null``,
because the lab's schema treats them as ``optional`` and rejects ``null``.
A decision holds at most one label, the first one written, as in
``jev-lab``. A label names its reviewer: ``human`` (a person, including a
review-surface ``user``) or ``model``, which must name the model id that
labelled, so calibration can report and gate on where its labels came from.
A ``blind`` label was derived by comparing a labeller's own answers with
Jev's (``raidar.scoring.judge.blind``) rather than by judging Jev's answer directly.
Like the store's ``log.jsonl``, it holds replay-varying facts (timestamps, request ids,
latency) and is never part of an Assessment key.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from raidar.scoring.judge.policies import LABELS, PolicyMode

AUDIT_DIR = "jev-audit"
LABELS_FILE = "labels.jsonl"


class DecisionStage(StrEnum):
    DETERMINISTIC = "deterministic"
    JEV = "jev"
    JEV_ERROR = "jev_error"
    SKIPPED = "skipped"


class SufficiencyLevel(StrEnum):
    """R1 of the evaluate-jev rubric, scored on the state template, not on Jev."""

    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    MISSING = "missing"


class _Record(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", alias_generator=to_camel, populate_by_name=True
    )


class InputSufficiency(_Record):
    """Whether the state held what the questions need; ``missing`` names absent inputs."""

    level: SufficiencyLevel
    missing: tuple[str, ...] = ()
    clipped: tuple[str, ...] = ()


class DecisionRecord(_Record):
    schema_: Literal[1] = Field(default=1, alias="schema")
    type: Literal["decision"] = "decision"
    timestamp: str
    request_id: str
    policy: str
    policy_version: str
    mode: PolicyMode
    question_set: str
    stage: DecisionStage
    rule: str | None = None
    subject: str
    trial: str | None = None
    state: Any = None
    questions: dict[str, Any] | None = None
    answers: dict[str, Any] | None = None
    resolved_model: str | None = None
    provider_response_id: str | None = None
    cost_usd: float = 0.0
    latency_ms: int | None = None
    error: str | None = None
    failure_reason: str | None = None
    http_status: int | None = None
    cache_key: str | None = None
    cache_hit: bool = False
    carried_from: str | None = None
    input_sufficiency: InputSufficiency | None = None
    verdict: str
    enforced: bool
    counts_toward_verdicts: bool
    labels: tuple[str, ...] = LABELS


class ReviewerKind(StrEnum):
    HUMAN = "human"
    MODEL = "model"


# Aliases a label source may use for a reviewer kind (the review surface says ``user``).
REVIEWER_ALIASES = {"user": ReviewerKind.HUMAN}


def reviewer_kind(raw: object) -> ReviewerKind:
    """The reviewer kind ``raw`` names (``human`` when absent; ``user`` is ``human``)."""
    if raw is None or raw == "":
        return ReviewerKind.HUMAN
    text = str(raw)
    if text in REVIEWER_ALIASES:
        return REVIEWER_ALIASES[text]
    try:
        return ReviewerKind(text)
    except ValueError:
        kinds = [kind.value for kind in ReviewerKind]
        raise ValueError(f"reviewer {text!r} is not one of {kinds}") from None


class LabelRecord(_Record):
    schema_: Literal[1] = Field(default=1, alias="schema")
    type: Literal["label"] = "label"
    timestamp: str
    request_id: str
    policy: str
    label: str
    reviewer: ReviewerKind = ReviewerKind.HUMAN
    reviewer_model: str | None = None
    blind: bool | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _model_reviewer_names_its_model(self) -> Self:
        if self.reviewer is ReviewerKind.MODEL and not (self.reviewer_model or "").strip():
            raise ValueError("a model reviewer's label needs reviewer_model (the model id)")
        if self.reviewer is ReviewerKind.HUMAN and self.reviewer_model is not None:
            raise ValueError("a human reviewer's label takes no reviewer_model")
        return self

    @property
    def reviewer_ref(self) -> str:
        """``human`` or ``model:<model id>``."""
        if self.reviewer is ReviewerKind.MODEL:
            return f"model:{self.reviewer_model}"
        return self.reviewer.value


def new_request_id() -> str:
    return f"jevp_{uuid.uuid4()}"


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, (line + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)


class AuditLog:
    """Append-only decision and label records for one assessment store."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def for_store(cls, store_root: Path) -> AuditLog:
        return cls(store_root / AUDIT_DIR)

    def policy_dir(self, policy: str) -> Path:
        return self.root / policy

    def decisions_path(self, record: DecisionRecord) -> Path:
        return self.policy_dir(record.policy) / f"{record.timestamp[:10]}.jsonl"

    def labels_path(self, policy: str) -> Path:
        return self.policy_dir(policy) / LABELS_FILE

    def append(self, record: DecisionRecord) -> DecisionRecord:
        _append_line(self.decisions_path(record), _line(record))
        return record

    def append_label(self, record: LabelRecord) -> LabelRecord:
        if record.label not in LABELS:
            raise ValueError(f"unknown Jev label {record.label!r}; expected one of {LABELS}")
        _append_line(self.labels_path(record.policy), _line(record))
        return record

    def decisions(self, policy: str) -> list[DecisionRecord]:
        directory = self.policy_dir(policy)
        if not directory.is_dir():
            return []
        files = sorted(path for path in directory.glob("*.jsonl") if path.name != LABELS_FILE)
        return [record for path in files for record in _read(path, DecisionRecord)]

    def labels(self, policy: str) -> dict[str, LabelRecord]:
        """The label of each labelled decision: the first one written wins."""
        first: dict[str, LabelRecord] = {}
        for record in _read(self.labels_path(policy), LabelRecord):
            first.setdefault(record.request_id, record)
        return first


def _line(record: _Record) -> str:
    return record.model_dump_json(by_alias=True, exclude_none=True)


def _read[RecordT: _Record](path: Path, model: type[RecordT]) -> list[RecordT]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [model.model_validate_json(line) for line in lines if line.strip()]
