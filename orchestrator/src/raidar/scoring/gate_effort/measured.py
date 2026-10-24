"""A value ``gate-effort`` measured, or the reason there is none."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from raidar.scoring.framework.result import AssessStatus, Observation


@dataclass(frozen=True)
class Measured:
    """A value, or why there is none."""

    value: float | None
    status: AssessStatus = AssessStatus.OK
    reason: str | None = None
    numerator: float | None = None
    denominator: float | None = None

    @classmethod
    def gap(cls, status: AssessStatus, reason: str) -> Measured:
        return cls(None, status, reason)

    @classmethod
    def not_applicable(cls, reason: str) -> Measured:
        return cls(None, AssessStatus.NOT_APPLICABLE, reason)

    @property
    def ok(self) -> bool:
        return self.status is AssessStatus.OK

    def observation(self, metric: str, subject: str) -> Observation:
        return Observation(
            metric=metric,
            subject=subject,
            status=self.status,
            value=self.value,
            reason=self.reason,
            numerator=self.numerator,
            denominator=self.denominator,
        )


def flag(value: bool) -> Measured:
    return Measured(1.0 if value else 0.0)


def same_for(metrics: Sequence[str], measured: Measured) -> dict[str, Measured]:
    """``measured`` for every metric id in ``metrics``."""
    return dict.fromkeys(metrics, measured)
