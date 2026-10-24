"""Find a trial's stored Assessments by recomputing their store keys.

The key is recomputed exactly as ``raidar assess --rescore`` computes it
(scorer identity, the lock's assessor config, tool lock and the trial's
input digests), so an Assessment is used only when the current scorer version
produced it from this trial's exact inputs; otherwise the scorer's result is
``None`` and analysis reads its values as ``unassessed``.

A trial whose inputs cannot be loaded (moved verifier bundle, missing snapshot)
is recorded in ``input_errors``, never silently read as unassessed; the
caller refuses it for a valid trial.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.engine.jobs.assessment import ASSESSMENT_CONFIG_FILE, lock_rescore_config
from raidar.evaluation.analysis.sources import AnalysisInputError, JobDir
from raidar.scoring.framework.catalog import ScorerRegistry
from raidar.scoring.framework.keys import scorer_identity, store_key
from raidar.scoring.framework.rescore import RescoreError
from raidar.scoring.framework.rescore_config import RescoreConfig, load_rescore_config
from raidar.scoring.framework.result import AssessResult
from raidar.scoring.framework.store import AssessmentStore, ScorerIdentity
from raidar.scoring.framework.trial_inputs import (
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
        self.input_errors: dict[Path, str] = {}

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
        except TrialInputError as exc:
            self.input_errors[trial_dir] = str(exc)
            return found
        inputs = context_inputs(ctx)
        for ref in self.scorer_refs:
            key = store_key(
                self.registry.get(ref),
                config.scorers.get(ref, {}),
                inputs,
                identity=self._identity(ref),
            ).key
            assessment = self.store.get(key)
            found[ref] = assessment.result if assessment is not None else None
        return found

    def valid_results(
        self, trial_dir: Path, config: RescoreConfig, *, trial_id: str, valid: bool
    ) -> dict[str, AssessResult | None]:
        """``results``; a valid trial whose inputs cannot be loaded is refused."""
        found = self.results(trial_dir, config)
        if valid and trial_dir in self.input_errors:
            raise AnalysisInputError(
                f"trial {trial_id} ({trial_dir}) cannot be assessed: "
                f"{self.input_errors[trial_dir]}; if its verifier bundle moved, pass "
                "--bundle-search DIR (bundles are found again by content digest)"
            )
        return found


def assessment_config(
    run: JobDir, override: Path | None, lock: Mapping[str, Any] | None = None
) -> RescoreConfig:
    """``override`` if given, else the lock's declared settings, else the job's file."""
    if override is None and lock is not None:
        try:
            return lock_rescore_config(lock)
        except ValueError as exc:
            raise AnalysisInputError(f"the lock's assessment settings are invalid: {exc}") from exc
    path = override or run.path / ASSESSMENT_CONFIG_FILE
    if not path.is_file():
        return RescoreConfig()
    try:
        return load_rescore_config(path)
    except RescoreError as exc:
        raise AnalysisInputError(str(exc)) from exc
