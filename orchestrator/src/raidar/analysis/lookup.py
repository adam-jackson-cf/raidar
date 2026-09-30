"""Find a trial's stored Assessments by recomputing their store keys.

The key is recomputed exactly as ``raidar assess --rescore`` computes it
(scorer identity, the run's locked assessor config, tool lock and the trial's
input digests), so an Assessment is used only when the current scorer version
produced it from this trial's exact inputs; otherwise the scorer's result is
``None`` and analysis reads its values as ``unassessed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raidar.analysis.sources import AnalysisInputError, RunDir
from raidar.execution.assessment import ASSESSMENT_CONFIG_FILE
from raidar.scorers.catalog import ScorerRegistry, tool_lock_for
from raidar.scorers.rescore import RescoreError, scorer_identity
from raidar.scorers.rescore_config import RescoreConfig, load_rescore_config
from raidar.scorers.sdk.result import AssessResult
from raidar.scorers.store import AssessmentStore, ScorerIdentity, assessment_key
from raidar.scorers.trial_inputs import (
    BundleCache,
    TrialInputError,
    context_inputs,
    load_trial_context,
)


@dataclass
class AssessmentLookup:
    """Finds a trial's stored Assessments by recomputing their keys."""

    store: AssessmentStore
    registry: ScorerRegistry
    scorer_refs: tuple[str, ...]
    bundles: BundleCache

    def __post_init__(self) -> None:
        self._identities: dict[str, ScorerIdentity] = {}

    def _identity(self, ref: str) -> ScorerIdentity:
        if ref not in self._identities:
            self._identities[ref] = scorer_identity(self.registry.get(ref))
        return self._identities[ref]

    def results(self, trial_dir: Path, config: RescoreConfig) -> dict[str, AssessResult | None]:
        found: dict[str, AssessResult | None] = dict.fromkeys(self.scorer_refs)
        if not (trial_dir / "result.json").is_file():
            return found
        try:
            ctx = load_trial_context(
                trial_dir, inventory_policy=config.inventory_policy, bundle_cache=self.bundles
            )
        except TrialInputError:
            return found
        inputs = context_inputs(ctx)
        for ref in self.scorer_refs:
            key = assessment_key(
                self._identity(ref),
                config=dict(config.scorers.get(ref, {})),
                tool_lock=tool_lock_for(self.registry.get(ref).manifest),
                inputs=inputs,
            )
            assessment = self.store.get(key)
            found[ref] = assessment.result if assessment is not None else None
        return found


def assessment_config(run: RunDir, override: Path | None) -> RescoreConfig:
    path = override or run.path / ASSESSMENT_CONFIG_FILE
    if not path.is_file():
        return RescoreConfig()
    try:
        return load_rescore_config(path)
    except RescoreError as exc:
        raise AnalysisInputError(str(exc)) from exc
