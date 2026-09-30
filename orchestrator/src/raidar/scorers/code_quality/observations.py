"""Turn checkpoint and transition measures into statused observations and evidence.

A component value is reported with its exact Decimal kept beside it, so the
problem-level mean is computed at the fork's precision rather than from the
float an Observation carries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from raidar.scorers.code_quality.checkpoint import CheckpointMeasure, Gap
from raidar.scorers.code_quality.evidence import (
    FileDigests,
    architecture_evidence,
    clone_evidence,
    erosion_evidence,
    regression_evidence,
    rework_evidence,
    ruff_evidence,
)
from raidar.scorers.code_quality.transition import TransitionMeasure
from raidar.scorers.inventory import FileRole
from raidar.scorers.sdk.result import (
    AssessStatus,
    Evidence,
    Exclusion,
    Observation,
    transition_subject,
)

_QUALITY_METRICS = ("verbosity", "erosion", "lint_findings", "production_loc")


@dataclass
class ObservationSet:
    observations: list[Observation] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    exclusions: list[Exclusion] = field(default_factory=list)
    exact: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    files: FileDigests = field(default_factory=FileDigests)

    def ok(self, metric: str, subject: str, value: Decimal | int) -> None:
        self.observations.append(
            Observation(metric=metric, subject=subject, status=AssessStatus.OK, value=float(value))
        )
        self.exact[(metric, subject)] = Decimal(value)

    def gap(self, metric: str, subject: str, gap: Gap) -> None:
        self.observations.append(
            Observation(metric=metric, subject=subject, status=gap.status, reason=gap.reason)
        )

    def by_metric(self, metric: str) -> list[Observation]:
        return [item for item in self.observations if item.metric == metric]


def _exclusions(observations: ObservationSet, measure: CheckpointMeasure) -> None:
    inventory = measure.inventory
    if inventory is None:
        return
    subject = measure.step.step
    observations.exclusions.extend(
        Exclusion(subject=subject, path=item.path, reason=item.reason)
        for item in inventory.exclusions
    )
    observations.exclusions.extend(
        Exclusion(subject=subject, path=item.path, reason=f"role:{item.role.value}")
        for item in inventory.files
        if item.role is not FileRole.PRODUCTION
    )


def _quality(observations: ObservationSet, measure: CheckpointMeasure) -> None:
    subject = measure.step.step
    measured = measure.quality()
    if isinstance(measured, Gap):
        for metric in _QUALITY_METRICS:
            observations.gap(metric, subject, measured)
        return
    quality, root = measured
    raw = quality.evidence
    observations.ok("verbosity", subject, quality.verbosity)
    observations.ok("erosion", subject, quality.erosion)
    observations.ok("lint_findings", subject, len(raw.ruff.diagnostics))
    observations.ok("production_loc", subject, raw.source_loc_denominator)
    files = observations.files
    observations.evidence.extend(ruff_evidence(files, "verbosity", subject, root, quality))
    observations.evidence.extend(clone_evidence(files, subject, root, quality))
    observations.evidence.extend(ruff_evidence(files, "lint_findings", subject, root, quality))
    observations.evidence.extend(erosion_evidence(files, subject, root, quality))


def checkpoint_observations(observations: ObservationSet, measure: CheckpointMeasure) -> None:
    """V, E, lint findings, production LOC and H for one checkpoint."""
    subject = measure.step.step
    _exclusions(observations, measure)
    _quality(observations, measure)
    measured = measure.graph()
    if isinstance(measured, Gap):
        observations.gap("architecture", subject, measured)
        return
    graph, root = measured
    observations.ok("architecture", subject, graph.architecture)
    observations.evidence.extend(architecture_evidence(observations.files, subject, root, graph))


def transition_observations(observations: ObservationSet, transition: TransitionMeasure) -> None:
    """R and G for one adjacent transition."""
    subject = transition_subject(transition.prior.step, transition.current.step)
    rework, regression, roots = transition.rework, transition.regression, transition.roots
    if isinstance(rework, Gap):
        observations.gap("rework", subject, rework)
    elif roots is not None:
        observations.ok("rework", subject, rework.rework)
        observations.evidence.extend(
            rework_evidence(observations.files, subject, roots[0], roots[1], rework)
        )
    if isinstance(regression, Gap):
        observations.gap("regression", subject, regression)
    elif regression.regression is not None and roots is not None:
        observations.ok("regression", subject, regression.regression)
        observations.evidence.extend(
            regression_evidence(observations.files, subject, roots[1], regression)
        )
