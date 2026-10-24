"""Validate a scorer's output against its manifest (plan F1).

A result is accepted only when every observation is a declared metric for a
subject valid at that metric's level, each (metric, subject) appears exactly
once, every declared (metric, subject) is present (as a value or an explicit
status), and every ``ok`` value fits the metric's type. The ownership,
duplicate and missing rules are the shared ones in ``ownership``.

Two rules tie a result to the stored run. Every evidence item must name a
file that exists for its subject's steps, with the recorded ``sha256`` and
spans inside the file, so evidence cannot be fabricated. A scorer that reads
snapshots may not report an ``ok`` checkpoint or transition observation, for a
metric that reads snapshots (``MetricSpec.inputs``), on a step whose assessor
inventory is invalid (``score_evidence_invalid``): that step is
``ineligible``, as in the SCB fork. Trajectory observations remain the
scorer's aggregation over eligible checkpoints.

The accepted result is returned in canonical order (manifest metric order,
then subject order; evidence and exclusions sorted), so the rendered bytes do
not depend on the order a scorer emitted things in.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.manifest import EvidenceInput, MetricLevel, ScorerManifest
from raidar.scorers.sdk.ownership import merge_unique, ordered_complete, reject_unowned
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    TRANSITION_SEPARATOR,
    AssessResult,
    AssessStatus,
    Evidence,
    EvidenceSource,
    Observation,
)

_SNAPSHOT_INPUTS = frozenset({EvidenceInput.SNAPSHOT_CURRENT, EvidenceInput.SNAPSHOT_PRIOR})


class InvalidObservationError(ValueError):
    """An observation's value does not fit its declared metric."""


class InvalidEvidenceError(ValueError):
    """An evidence item does not point at real content of the run it assesses."""


@dataclass(frozen=True, order=True)
class MetricKey:
    metric: str
    subject: str

    def __str__(self) -> str:
        return f"{self.metric}[{self.subject}]"


def _required_keys(manifest: ScorerManifest, ctx: AssessContext) -> list[MetricKey]:
    return [
        MetricKey(metric.id, subject)
        for metric in manifest.metrics
        for subject in ctx.subjects(metric.level)
    ]


def _check_values(manifest: ScorerManifest, observations: Iterable[Observation]) -> None:
    for observation in observations:
        metric = manifest.metric(observation.metric)
        if metric is None or observation.value is None:
            continue
        problem = metric.admits(observation.value)
        if problem is not None:
            raise InvalidObservationError(
                f"Scorer {manifest.ref} metric {observation.metric}[{observation.subject}]: "
                f"{problem}"
            )


def _subject_steps(ctx: AssessContext, subject: str) -> list[StepInputs]:
    """The steps whose stored trees a subject's evidence may point into."""
    if subject == TRAJECTORY_SUBJECT:
        return list(ctx.steps)
    names = subject.split(TRANSITION_SEPARATOR)
    return [ctx.step(name) for name in reversed(names)]


def _line_count(data: bytes) -> int:
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def _evidence_problem(base: Path, item: Evidence) -> str | None:
    relative = PurePosixPath(item.path)
    if relative.is_absolute() or ".." in relative.parts:
        return "path must be relative to the stored tree"
    path = base / relative
    root = base.resolve()
    if not path.is_file() or not path.resolve().is_relative_to(root):
        return "no such file"
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != item.sha256:
        return "sha256 does not match the file"
    lines = _line_count(data)
    last = max(max(span.line, span.end_line or span.line) for span in item.spans)
    if last > lines:
        return f"span line {last} is past the end of the file ({lines} lines)"
    return None


def _check_evidence(manifest: ScorerManifest, ctx: AssessContext, result: AssessResult) -> None:
    for item in result.evidence:
        problems: list[str] = []
        for step in _subject_steps(ctx, item.subject):
            base = step.snapshot if item.source is EvidenceSource.SNAPSHOT else step.trace_dir
            problem = "step has no stored tree" if base is None else _evidence_problem(base, item)
            if problem is None:
                break
            problems.append(f"{step.step}: {problem}")
        else:
            raise InvalidEvidenceError(
                f"Scorer {manifest.ref} evidence {item.metric}[{item.subject}] "
                f"{item.source.value}:{item.path}: {'; '.join(problems)}"
            )


def _ineligible_steps(ctx: AssessContext) -> set[str]:
    """Steps whose assessor inventory is invalid (``score_evidence_invalid``)."""
    ineligible = set()
    for step in ctx.steps:
        inventory = ctx.inventory(step.step)
        if inventory is not None and not inventory.eligible:
            ineligible.add(step.step)
    return ineligible


def _step_bound(manifest: ScorerManifest, observation: Observation) -> bool:
    """An ok checkpoint or transition value of a metric that reads snapshots."""
    metric = manifest.metric(observation.metric)
    return (
        observation.status is AssessStatus.OK
        and observation.subject != TRAJECTORY_SUBJECT
        and metric is not None
        and bool(_SNAPSHOT_INPUTS.intersection(manifest.metric_inputs(metric)))
    )


def _check_eligibility(
    manifest: ScorerManifest, ctx: AssessContext, observations: Iterable[Observation]
) -> None:
    if not _SNAPSHOT_INPUTS.intersection(manifest.evidence_required):
        return
    ineligible = _ineligible_steps(ctx)
    for observation in observations:
        if not _step_bound(manifest, observation):
            continue
        blocked = ineligible.intersection(observation.subject.split(TRANSITION_SEPARATOR))
        if blocked:
            raise InvalidObservationError(
                f"Scorer {manifest.ref} metric {observation.metric}[{observation.subject}]: "
                f"snapshot evidence of {sorted(blocked)} is invalid; report it as ineligible"
            )


def validate_result(
    manifest: ScorerManifest, ctx: AssessContext, result: AssessResult
) -> AssessResult:
    """Accept a result and return it in canonical order, or raise ``ValueError``."""
    reject_unowned(
        (observation.metric for observation in result.observations),
        {metric.id for metric in manifest.metrics},
        owner=manifest.ref,
    )
    required = _required_keys(manifest, ctx)
    keys = [MetricKey(item.metric, item.subject) for item in result.observations]
    reject_unowned(keys, set(required), owner=manifest.ref)
    found: dict[MetricKey, Observation] = {}
    merge_unique(found, zip(keys, result.observations, strict=True))
    ordered = ordered_complete(found, required)
    _check_values(manifest, ordered)
    _check_eligibility(manifest, ctx, ordered)
    reject_unowned(
        (MetricKey(item.metric, item.subject) for item in result.evidence),
        set(required),
        owner=manifest.ref,
    )
    _check_evidence(manifest, ctx, result)
    step_subjects = set(ctx.subjects(MetricLevel.CHECKPOINT)) | set(
        ctx.subjects(MetricLevel.TRAJECTORY)
    )
    unknown = sorted({item.subject for item in result.exclusions} - step_subjects)
    if unknown:
        raise ValueError(f"Scorer {manifest.ref} excluded files for unknown subjects {unknown}")
    return result.model_copy(
        update={
            "observations": tuple(ordered),
            "evidence": tuple(
                sorted(
                    result.evidence,
                    key=lambda item: (
                        item.metric,
                        item.subject,
                        item.source.value,
                        item.path,
                        tuple(span.line for span in item.spans),
                        item.kind,
                        item.message,
                    ),
                )
            ),
            "exclusions": tuple(
                sorted(result.exclusions, key=lambda item: (item.subject, item.path, item.reason))
            ),
        }
    )


def validate_scorer_set(manifests: Iterable[ScorerManifest]) -> None:
    """Scorers in one assessment plan must have disjoint metric namespaces."""
    qualified: dict[str, str] = {}
    for manifest in manifests:
        merge_unique(
            qualified,
            ((manifest.qualified(metric.id), manifest.ref) for metric in manifest.metrics),
        )
