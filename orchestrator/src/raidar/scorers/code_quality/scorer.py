"""``code-quality``: the SCB fork's V/E/H per checkpoint and R/G per transition.

Implements the manifest ``builtin_manifests.CODE_QUALITY`` (plan G2/G3):

- ``verbosity``, ``erosion``, ``architecture``, ``lint_findings`` and
  ``production_loc`` for every planned checkpoint;
- ``rework`` and ``regression`` for every adjacent transition, and
  ``regression_parity_excluded_cases``: how many grader cases G left out
  (``coverage_reader``);
- ``problem_*``: the plain mean of each component over every subject (the
  fork's problem-level component), available only when every subject has a
  value. The fork scores an unproduced checkpoint as a declared zero; here the
  problem-level mean takes the most severe status of its missing subjects
  (``error`` > ``ineligible`` > ``unavailable`` > ...), and a mean missing
  only unreached checkpoints (``not_applicable`` subjects, e.g. after
  halt_on_failure) is ``unavailable``, because an unavailable value is never
  zero (F6). Only a one-checkpoint problem's R and G mean is
  ``not_applicable``: it has no transitions.

H needs the configured entrypoint to be a file in the snapshot. The fork
builds an empty graph (H = 1) when it is absent, for example after an agent
renamed or deleted it; here H is ``ineligible`` (``entry_file_absent``).

R and G follow the fork's live producers exactly (see ``diffs``), so every
component keeps its ``scb-exact`` label. G's two run evidences come from the trial's own
coverage pass, or, for a step whose trial deferred it, from the offline run in the
Assessment store (``coverage_reader``); a step not yet produced there makes G
``unavailable`` and the Assessment provisional (``awaiting_answers``).

Config (``ctx.config``): ``entry_file``, the snapshot-relative entrypoint the
call graph (H) starts from, as the fork's environment formats it (for example
``forge.py``). Without it H is ``unavailable`` and V/E/R/G are unaffected.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from raidar.scorers.builtin_manifests import CODE_QUALITY
from raidar.scorers.code_quality.arithmetic import mean
from raidar.scorers.code_quality.checkpoint import CheckpointMeasure, Gap, measure_checkpoint
from raidar.scorers.code_quality.coverage_reader import CoverageReader
from raidar.scorers.code_quality.measurement import ToolEnvironment, provisioned_environment
from raidar.scorers.code_quality.observations import (
    ObservationSet,
    checkpoint_observations,
    excluded_observation,
    transition_observations,
)
from raidar.scorers.code_quality.transition import measure_transitions
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.manifest import ScorerManifest, SdkModel
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
    Provenance,
)

_PROBLEM_METRICS = {
    "problem_verbosity": "verbosity",
    "problem_erosion": "erosion",
    "problem_architecture": "architecture",
    "problem_rework": "rework",
    "problem_regression": "regression",
}
_STATUS_PRECEDENCE = (
    AssessStatus.ERROR,
    AssessStatus.INELIGIBLE,
    AssessStatus.UNAVAILABLE,
    AssessStatus.INSUFFICIENT_EVIDENCE,
    AssessStatus.UNSUPPORTED,
    AssessStatus.NOT_APPLICABLE,
)


class CodeQualityConfig(SdkModel):
    """Scorer config: the call graph's entrypoint (fork ``format_entry_file`` output)."""

    entry_file: str | None = None


def _first_status(statuses: Iterable[AssessStatus]) -> AssessStatus:
    present = set(statuses)
    return next(status for status in _STATUS_PRECEDENCE if status in present)


def problem_mean(
    metric: str, component: Sequence[Observation], exact: Mapping[tuple[str, str], Decimal]
) -> Observation:
    """The fork's problem-level component: the mean over every subject, or why not."""
    missing = [item for item in component if item.status is not AssessStatus.OK]
    if not component:
        return Observation(
            metric=metric,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.NOT_APPLICABLE,
            reason="a single-checkpoint problem has no transitions",
        )
    if missing:
        # A subject that is only not_applicable (an unreached checkpoint) still leaves
        # the problem-level mean unmeasured: that is unavailable, never the
        # not_applicable a one-checkpoint problem's R and G report.
        status = _first_status(item.status for item in missing)
        return Observation(
            metric=metric,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.UNAVAILABLE if status is AssessStatus.NOT_APPLICABLE else status,
            reason="no value for "
            + "; ".join(f"{item.subject} ({item.status.value})" for item in missing),
        )
    value = mean([exact[(item.metric, item.subject)] for item in component])
    return Observation(
        metric=metric, subject=TRAJECTORY_SUBJECT, status=AssessStatus.OK, value=float(value)
    )


def _provenance(measures: Sequence[CheckpointMeasure]) -> Provenance:
    versions: dict[str, str] = {}
    for measure in measures:
        measured = measure.quality()
        if not isinstance(measured, Gap):
            quality = measured[0]
            versions["ruff"] = quality.evidence.ruff.version.removeprefix("ruff ")
            versions["cpython"] = quality.evidence.interpreter.version.split()[0]
    return Provenance(tool_versions=versions)


def _result(observations: ObservationSet, measures: Sequence[CheckpointMeasure]) -> AssessResult:
    items = observations.observations
    if any(item.status is AssessStatus.OK for item in items):
        return AssessResult(
            status=AssessStatus.OK,
            observations=tuple(items),
            evidence=tuple(observations.evidence),
            exclusions=tuple(observations.exclusions),
            provenance=_provenance(measures),
        )
    status = _first_status(item.status for item in items)
    reason = next(item.reason for item in items if item.status is status)
    return AssessResult(
        status=status,
        reason=reason,
        observations=tuple(items),
        exclusions=tuple(observations.exclusions),
    )


@dataclass
class CodeQualityScorer:
    """Observation-only scorer; the tool environment is provisioned on first use."""

    tools_factory: Callable[[], ToolEnvironment] = provisioned_environment
    store_root: Path | None = None
    _tools: ToolEnvironment | None = field(default=None, init=False, repr=False)
    _missing: list[str] = field(default_factory=list, init=False, repr=False)

    @property
    def manifest(self) -> ScorerManifest:
        return CODE_QUALITY

    def awaiting_answers(self) -> bool:
        """True when a deferred step's offline coverage run was not produced yet."""
        return bool(self._missing)

    def _environment(self) -> ToolEnvironment:
        if self._tools is None:
            self._tools = self.tools_factory()
        return self._tools

    def assess(self, ctx: AssessContext) -> AssessResult:
        self._missing = []
        config = CodeQualityConfig.model_validate(dict(ctx.config))
        if not any(step.reached and step.snapshot is not None for step in ctx.steps):
            return statused_result(
                CODE_QUALITY, ctx, AssessStatus.UNAVAILABLE, "no stored snapshot to measure"
            )
        try:
            tools = self._environment()
        except (OSError, RuntimeError, ValueError) as error:
            return statused_result(
                CODE_QUALITY,
                ctx,
                AssessStatus.UNAVAILABLE,
                f"measurement_environment_mismatch: tool environment unavailable: {error}",
            )
        measures = [measure_checkpoint(ctx, step, tools, config.entry_file) for step in ctx.steps]
        reader = CoverageReader(self.store_root)
        observations = ObservationSet()
        for measure in measures:
            checkpoint_observations(observations, measure)
        for transition in measure_transitions(measures, reader.run_evidence):
            transition_observations(observations, transition)
            observations.observations.append(excluded_observation(transition, reader.excluded))
        for metric, component in _PROBLEM_METRICS.items():
            observations.observations.append(
                problem_mean(metric, observations.by_metric(component), observations.exact)
            )
        self._missing = reader.missing
        return _result(observations, measures)


def code_quality_scorer() -> CodeQualityScorer:
    """Registry factory for ``code-quality`` without a store (offline runs unavailable)."""
    return CodeQualityScorer()


def code_quality_scorer_for_store(store_root: Path) -> CodeQualityScorer:
    """Store-bound factory: reads deferred steps' offline coverage runs from the store."""
    return CodeQualityScorer(store_root=store_root)
