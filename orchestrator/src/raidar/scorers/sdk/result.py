"""What a scorer returns: statused observations, evidence, exclusions, provenance.

An unavailable value is never zero (F6): every non-``ok`` observation carries
no value and a reason, and an ``ok`` observation must carry a value. Evidence
is traceable to ``file:line`` in a snapshot. Everything here is deterministic
for a deterministic scorer, so a replay renders byte-identically; run-time
facts that vary between replays (such as duration) live in
``RunStats``, which is logged beside the store and never part of an Assessment.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from raidar.scorers.sdk.manifest import SdkModel

TRAJECTORY_SUBJECT = "trajectory"
TRANSITION_SEPARATOR = "->"


def transition_subject(prior: str, current: str) -> str:
    return f"{prior}{TRANSITION_SEPARATOR}{current}"


class AssessStatus(StrEnum):
    """Status of a whole result or of one observation. Only ``ok`` carries a value."""

    OK = "ok"
    NOT_APPLICABLE = "not_applicable"
    INELIGIBLE = "ineligible"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class Observation(SdkModel):
    """One metric value for one subject (a checkpoint, a transition or the trajectory)."""

    metric: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    status: AssessStatus
    value: float | None = None
    numerator: float | None = None
    denominator: float | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _value_matches_status(self) -> Self:
        where = f"{self.metric}[{self.subject}]"
        if self.status is AssessStatus.OK:
            if self.value is None:
                raise ValueError(f"{where}: an ok observation needs a value")
        elif self.value is not None or self.numerator is not None or self.denominator is not None:
            raise ValueError(
                f"{where}: a {self.status.value} observation carries no value (never zero)"
            )
        elif not self.reason:
            raise ValueError(f"{where}: a {self.status.value} observation needs a reason")
        for name in ("value", "numerator", "denominator"):
            number = getattr(self, name)
            if number is not None and not math.isfinite(number):
                raise ValueError(f"{where}: {name} must be finite")
        self._ratio_matches_value(where)
        return self

    def _ratio_matches_value(self, where: str) -> None:
        """A reported numerator/denominator pair must be the value it explains."""
        if self.numerator is None and self.denominator is None:
            return
        if self.numerator is None or self.denominator is None or self.value is None:
            raise ValueError(f"{where}: numerator and denominator are reported together")
        if self.denominator == 0:
            raise ValueError(
                f"{where}: a zero denominator has no value; report it as not_applicable"
            )
        ratio = self.numerator / self.denominator
        if not math.isclose(self.value, ratio, rel_tol=1e-9, abs_tol=1e-12):
            raise ValueError(f"{where}: value {self.value} is not numerator/denominator ({ratio})")


class Span(SdkModel):
    line: int = Field(ge=1)
    end_line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end_line is not None and self.end_line < self.line:
            raise ValueError("span end_line precedes line")
        return self


class EvidenceSource(StrEnum):
    """Which stored tree an evidence ``path`` is relative to."""

    SNAPSHOT = "snapshot"
    TRACE = "trace"


class Evidence(SdkModel):
    """A finding at ``path`` with at least one line span.

    ``path`` is relative to a step's snapshot (or, with ``source=trace``, its
    agent trace directory). Validation checks the file exists for the
    subject's steps, ``sha256`` is its content and every span lies inside it.
    """

    metric: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    source: EvidenceSource = EvidenceSource.SNAPSHOT
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: str = Field(min_length=1)
    spans: tuple[Span, ...] = Field(min_length=1)
    message: str = ""

    def locations(self) -> tuple[str, ...]:
        return tuple(f"{self.path}:{span.line}" for span in self.spans)


class Exclusion(SdkModel):
    """A file left out of measurement; exclusions are accounted, never silent."""

    subject: str = Field(min_length=1)
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Provenance(SdkModel):
    """Deterministic facts about how the result was produced."""

    tool_versions: dict[str, str] = Field(default_factory=dict)
    jev_model_resolved: str | None = None
    policy_modes: dict[str, str] = Field(default_factory=dict)


class AssessResult(SdkModel):
    status: AssessStatus
    reason: str | None = None
    observations: tuple[Observation, ...]
    evidence: tuple[Evidence, ...] = ()
    exclusions: tuple[Exclusion, ...] = ()
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def _status_matches_observations(self) -> Self:
        """``ok`` exactly when some observation is ``ok``; otherwise explain why."""
        measured = any(item.status is AssessStatus.OK for item in self.observations)
        if self.status is AssessStatus.OK:
            if not measured:
                raise ValueError("an ok result needs at least one ok observation")
            return self
        if not self.reason:
            raise ValueError(f"a {self.status.value} result needs a reason")
        if measured:
            raise ValueError(f"a {self.status.value} result cannot carry ok observations")
        return self


class RunStats(SdkModel):
    """Per-run facts that legitimately differ between replays; logged, never keyed."""

    duration_s: float = Field(ge=0)


# touched
