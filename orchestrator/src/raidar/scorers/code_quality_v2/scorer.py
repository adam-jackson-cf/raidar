"""``code-quality@2``: code-quality@1 whose G reads an offline run when the trial deferred it.

``code-quality@1`` (``raidar.scorers.code_quality``) is released and unchanged.
Its regression breadth (G) reads the verifier's per-test coverage evidence
that the trial wrote beside each step record (``g-kernel-input.json`` and the
record's parity verdict). A trial whose lock sets
``protocol.verifier_coverage: offline`` grades without that coverage pass
(``record.coverage.enabled`` false), so @1 would read every G as
``ineligible`` (coverage run disabled). @2 is @1 in every metric, status,
reason and evidence item, except where G's two run evidences come from:

- a step whose trial made the coverage pass: the step record and
  ``g-kernel-input.json``, exactly as @1 reads them;
- a step whose trial deferred it: the offline coverage run that
  ``raidar assess --rescore`` makes into the Assessment store before scoring
  (``raidar.verifier.coverage_offline``; read through
  ``raidar.verifier.coverage_store``), keyed by the step's bundle digest, step
  and snapshot digest. It holds the same parity verdict and the same
  fork-shaped G input the in-trial pass would have written, and @2 applies @1's
  rules to them (a failed parity check is ``ineligible``,
  ``coverage_parity_failed``). A run not produced yet makes G ``unavailable``
  and the Assessment provisional (``awaiting_answers``), so a later rescore
  completes it instead of reusing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from raidar.scorers.builtin_manifests import CODE_QUALITY
from raidar.scorers.code_quality.checkpoint import measure_checkpoint
from raidar.scorers.code_quality.observations import (
    ObservationSet,
    checkpoint_observations,
    transition_observations,
)
from raidar.scorers.code_quality.scorer import (
    _PROBLEM_METRICS,
    CodeQualityConfig,
    CodeQualityScorer,
    _result,
    problem_mean,
)
from raidar.scorers.code_quality_v2.transition import (
    OFFLINE_NOT_PRODUCED,
    OfflineCoverageReader,
    measure_transitions,
)
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.manifest import ScorerManifest
from raidar.scorers.sdk.result import AssessResult, AssessStatus

CODE_QUALITY_V2 = CODE_QUALITY.model_copy(
    update={
        "version": "2",
        "description": (
            "SCB-fork code-quality and inertia components over the per-step snapshots "
            "(code-quality@1); a step graded without the in-trial coverage pass reads G's "
            "coverage run from the offline run in the Assessment store."
        ),
    }
)


@dataclass
class CodeQualityV2Scorer(CodeQualityScorer):
    """code-quality@1 whose G reads a deferred step's coverage run from the store."""

    store_root: Path | None = None
    _missing: list[str] = field(default_factory=list, init=False, repr=False)

    @property
    def manifest(self) -> ScorerManifest:
        return CODE_QUALITY_V2

    def awaiting_answers(self) -> bool:
        """True when a deferred step's offline coverage run was not produced yet."""
        return bool(self._missing)

    def assess(self, ctx: AssessContext) -> AssessResult:
        self._missing = []
        config = CodeQualityConfig.model_validate(dict(ctx.config))
        if not any(step.reached and step.snapshot is not None for step in ctx.steps):
            return statused_result(
                CODE_QUALITY_V2, ctx, AssessStatus.UNAVAILABLE, "no stored snapshot to measure"
            )
        try:
            tools = self._environment()
        except (OSError, RuntimeError, ValueError) as error:
            return statused_result(
                CODE_QUALITY_V2,
                ctx,
                AssessStatus.UNAVAILABLE,
                f"measurement_environment_mismatch: tool environment unavailable: {error}",
            )
        measures = [measure_checkpoint(ctx, step, tools, config.entry_file) for step in ctx.steps]
        reader = OfflineCoverageReader(self.store_root)
        observations = ObservationSet()
        for measure in measures:
            checkpoint_observations(observations, measure)
        for transition in measure_transitions(measures, reader):
            transition_observations(observations, transition)
        for metric, component in _PROBLEM_METRICS.items():
            observations.observations.append(
                problem_mean(metric, observations.by_metric(component), observations.exact)
            )
        self._missing = reader.missing
        return _result(observations, measures)


def code_quality_v2_scorer() -> CodeQualityV2Scorer:
    """Registry factory for ``code-quality@2`` without a store (offline runs unavailable)."""
    return CodeQualityV2Scorer()


def code_quality_v2_scorer_for_store(
    store_root: Path, *, replay_only: bool = False, trial: str | None = None
) -> CodeQualityV2Scorer:
    """Store-bound factory: reads deferred steps' offline coverage runs from the store."""
    del replay_only, trial
    return CodeQualityV2Scorer(store_root=store_root)


__all__ = [
    "CODE_QUALITY_V2",
    "OFFLINE_NOT_PRODUCED",
    "CodeQualityV2Scorer",
    "OfflineCoverageReader",
    "code_quality_v2_scorer",
    "code_quality_v2_scorer_for_store",
    "measure_transitions",
]
