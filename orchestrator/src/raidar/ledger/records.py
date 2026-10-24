"""Ledger records: fault categories (FCR) and remedies (M) as Markdown with YAML frontmatter."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

FAULT_ID = re.compile(r"^FCR-\d{3,}$")
REMEDY_ID = re.compile(r"^M-\d{3,}$")
_REF_LINE = re.compile(r"^(?P<path>[^\s:#]+)(?::\d+(?:-\d+)?)?(?:#\S+)?$")

FaultStatus = Literal[
    "open", "targeted", "mitigated", "resolved", "reopened", "not-reproducible", "routed"
]
RemedyStatus = Literal["proposed", "locked", "executed", "measured", "adopted", "rejected"]

FAULT_STATUSES: tuple[str, ...] = (
    "open",
    "targeted",
    "mitigated",
    "resolved",
    "reopened",
    "not-reproducible",
    "routed",
)
REMEDY_STATUSES: tuple[str, ...] = (
    "proposed",
    "locked",
    "executed",
    "measured",
    "adopted",
    "rejected",
)

FAULT_INITIAL = "open"
REMEDY_INITIAL = "proposed"

FAULT_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"targeted", "not-reproducible", "routed"}),
    "targeted": frozenset({"mitigated", "resolved"}),
    "mitigated": frozenset({"resolved"}),
    "resolved": frozenset({"reopened"}),
    "reopened": frozenset({"targeted"}),
    "not-reproducible": frozenset(),
    "routed": frozenset(),
}
REMEDY_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"locked", "rejected"}),
    "locked": frozenset({"executed", "rejected"}),
    "executed": frozenset({"measured", "rejected"}),
    "measured": frozenset({"adopted", "rejected"}),
    "adopted": frozenset(),
    "rejected": frozenset(),
}

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def ref_problem(ref: str) -> str | None:
    """Return why an evidence reference is malformed, or None when it is a relative path."""
    match = _REF_LINE.fullmatch(ref)
    if match is None:
        return "contains whitespace or is not `path[:line[-line]][#anchor]`"
    path = PurePosixPath(match.group("path"))
    if path.is_absolute() or ".." in path.parts or "\\" in ref:
        return "must be a relative repo path without `..`"
    return None


def _check_refs(refs: list[str]) -> list[str]:
    for ref in refs:
        problem = ref_problem(ref)
        if problem:
            raise ValueError(f"evidence ref {ref!r} {problem}")
    return refs


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HistoryEntry(_Strict):
    status: str
    date: dt.date
    evidence: list[str] = Field(default_factory=list)
    reason: Text

    _refs = field_validator("evidence")(_check_refs)


class FaultRecord(_Strict):
    id: str = Field(pattern=FAULT_ID.pattern)
    title: Text
    category: Literal[
        "Behavioral", "Integration", "Recovery", "Structural", "Process", "Measurement"
    ]
    owner: str = Field(pattern=r"^(raidar|subject):[A-Za-z0-9_.-]+$")
    status: FaultStatus
    evidence: list[str] = Field(min_length=1)
    configurations: list[Text] = Field(min_length=1)
    impact: Text
    hypothesis: Text
    criterion: Text | None = None
    target: str | None = None
    solution: str | None = None
    validation: str | None = None
    status_history: list[HistoryEntry] = Field(min_length=1)

    _refs = field_validator("evidence")(_check_refs)


class RemedyRecord(_Strict):
    id: str = Field(pattern=REMEDY_ID.pattern)
    title: Text
    faults: list[str] = Field(min_length=1)
    lever: Text
    placement: Text
    home: str = Field(pattern=r"^(raidar|subject:[A-Za-z0-9_.-]+@[A-Za-z0-9_.+-]+)$")
    rationale: Text
    proof_plan: Text
    experiment: str | None = None
    status: RemedyStatus
    status_history: list[HistoryEntry] = Field(min_length=1)
    outcome: str | None = None

    @field_validator("faults")
    @classmethod
    def _fault_ids(cls, faults: list[str]) -> list[str]:
        bad = [f for f in faults if not FAULT_ID.fullmatch(f)]
        if bad:
            raise ValueError(f"not fault ids: {bad}")
        return faults

    @field_validator("experiment")
    @classmethod
    def _experiment_path(cls, value: str | None) -> str | None:
        if value is not None and (problem := ref_problem(value)):
            raise ValueError(f"experiment path {value!r} {problem}")
        return value


Record = FaultRecord | RemedyRecord


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split ``---`` delimited YAML frontmatter from the Markdown body."""
    if not text.startswith("---\n"):
        raise ValueError("missing `---` frontmatter at the start of the file")
    end = text.find("\n---\n", 3)
    if end < 0:
        raise ValueError("frontmatter is not closed by a `---` line")
    return text[4 : end + 1], text[end + 5 :]


def parse_record(path: Path, model: type[FaultRecord] | type[RemedyRecord]) -> Record:
    """Parse and validate one record file; raise ValueError with a readable message."""
    front, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    try:
        data = yaml.safe_load(front)
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '<record>'}: {e['msg']}"
                for e in exc.errors()
            )
        ) from exc
