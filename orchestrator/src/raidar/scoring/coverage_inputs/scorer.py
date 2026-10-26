"""The shared body of the two coverage scorers: per step, a reading and its recorded evidence.

A coverage scorer (``definition-coverage``, ``test-coverage``) reads one step's retained artifacts
(``records``), asks Jev through a host-side ``RecordedJev`` and reports each step's ``StepReading``.
Whatever it learned per unit or criterion is kept in the Assessment's raw payloads:
``readings.json`` (every question, its level probabilities or why it is undecided, and the calls
that carried it) and ``jev/<step>/call-NNNN.*`` (each attempt's raw request body, raw response body
and interpreted result, never the key). The observations are summaries of the readings: counts and
a mean expected level, never a pass or fail. An Assessment with an undecided question is
provisional (``awaiting_answers``): a later rescore with a working key completes it.
"""

from __future__ import annotations

import json
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raidar.judge.live import RecordedJev
from raidar.scoring.coverage_inputs.asking import Asker
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.manifest import ScorerManifest
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation, Provenance

JevFactory = Callable[[Path], RecordedJev]
READINGS = "readings.json"
JEV_DIR = "jev"


@dataclass(frozen=True)
class StepReading:
    """One step's outcome: a status, metric values and the record kept for it."""

    status: AssessStatus
    reason: str | None = None
    values: Mapping[str, float] = field(default_factory=dict)
    gaps: Mapping[str, str] = field(default_factory=dict)
    record: Mapping[str, Any] = field(default_factory=dict)
    undecided: int = 0
    resolved_model: str | None = None


class CoverageScorer(ABC):
    """Template: subclasses give the manifest, the question set and ``read``."""

    question_set: str

    def __init__(self, jev: JevFactory | None = None) -> None:
        self._jev = jev or RecordedJev
        self._undecided = 0

    @property
    @abstractmethod
    def manifest(self) -> ScorerManifest: ...

    @abstractmethod
    def read(self, inputs: StepInputs, asker: Asker) -> StepReading:
        """Read one reached step."""

    def awaiting_answers(self) -> bool:
        """True when some question stayed undecided (a later rescore may decide it)."""
        return self._undecided > 0

    def assess(self, ctx: AssessContext) -> AssessResult:
        return self.assess_with_raw(ctx)[0]

    def assess_with_raw(self, ctx: AssessContext) -> tuple[AssessResult, Mapping[str, bytes]]:
        self._undecided = 0
        readings: dict[str, StepReading] = {}
        with tempfile.TemporaryDirectory(prefix="raidar-coverage-") as scratch:
            root = Path(scratch)
            for inputs in ctx.steps:
                if not inputs.reached:
                    readings[inputs.step] = StepReading(
                        AssessStatus.NOT_APPLICABLE, "checkpoint not reached"
                    )
                    continue
                readings[inputs.step] = self.read(inputs, Asker(self._jev(root / inputs.step)))
            payloads = _payloads(root)
        self._undecided = sum(reading.undecided for reading in readings.values())
        payloads[READINGS] = _readings_bytes(self.manifest.ref, self.question_set, readings)
        return self._result(readings), payloads

    def _result(self, readings: Mapping[str, StepReading]) -> AssessResult:
        observations = tuple(
            self._observation(metric.id, step, reading)
            for metric in self.manifest.metrics
            for step, reading in readings.items()
        )
        models = sorted({r.resolved_model for r in readings.values() if r.resolved_model})
        provenance = Provenance(jev_model_resolved=models[0] if models else None)
        if any(item.status is AssessStatus.OK for item in observations):
            return AssessResult(
                status=AssessStatus.OK, observations=observations, provenance=provenance
            )
        status, reason = _unmeasured(readings)
        return AssessResult(
            status=status, reason=reason, observations=observations, provenance=provenance
        )

    @staticmethod
    def _observation(metric: str, step: str, reading: StepReading) -> Observation:
        if reading.status is not AssessStatus.OK:
            return Observation(
                metric=metric, subject=step, status=reading.status, reason=reading.reason
            )
        if metric in reading.values:
            return Observation(
                metric=metric, subject=step, status=AssessStatus.OK, value=reading.values[metric]
            )
        return Observation(
            metric=metric,
            subject=step,
            status=AssessStatus.INSUFFICIENT_EVIDENCE,
            reason=reading.gaps.get(metric, "not measured"),
        )


def _unmeasured(readings: Mapping[str, StepReading]) -> tuple[AssessStatus, str]:
    """The one status (else insufficient evidence) and the reasons of a result with no value."""
    statuses = {reading.status for reading in readings.values()}
    status = next(iter(statuses)) if len(statuses) == 1 else AssessStatus.INSUFFICIENT_EVIDENCE
    reasons = sorted({reading.reason or status.value for reading in readings.values()})
    return status, "; ".join(reasons)


def _payloads(root: Path) -> dict[str, bytes]:
    return {
        f"{JEV_DIR}/{path.relative_to(root).as_posix()}": path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _readings_bytes(ref: str, question_set: str, readings: Mapping[str, StepReading]) -> bytes:
    document = {
        "scorer": ref,
        "question_set": question_set,
        "steps": {
            step: {
                "status": reading.status.value,
                "reason": reading.reason,
                "values": dict(reading.values),
                "gaps": dict(reading.gaps),
                **dict(reading.record),
            }
            for step, reading in readings.items()
        },
    }
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
