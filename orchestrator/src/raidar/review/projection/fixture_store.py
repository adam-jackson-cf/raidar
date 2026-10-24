"""Store the synthetic fixture's Assessments where ``raidar assess --rescore`` looks."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from raidar.review.projection.fixture_assessments import (
    MEASURED_SCORERS,
    QualityPlan,
    quality_result,
)
from raidar.scorers.builtin_manifests import CODE_QUALITY
from raidar.scorers.catalog import ScorerRegistry, default_scorer_registry
from raidar.scorers.keys import store_key
from raidar.scorers.rescore import assess
from raidar.scorers.rescore_config import RescoreConfig
from raidar.scorers.store import Assessment, AssessmentStore
from raidar.scorers.trial_inputs import context_inputs, load_trial_context


class FixtureAssessments:
    """The fixture's Assessment store and the scorer registry its keys come from."""

    def __init__(self, store_dir: Path) -> None:
        self._store = AssessmentStore(store_dir)
        self._registry: ScorerRegistry = default_scorer_registry()

    def store_trial(self, trial_dir: Path, settings: RescoreConfig, plan: QualityPlan) -> None:
        """Store the fixture trial's Assessments exactly where ``raidar assess --rescore`` looks."""
        ctx = load_trial_context(trial_dir, inventory_policy=settings.inventory_policy)
        for ref in MEASURED_SCORERS:
            scorer_ctx = replace(ctx, config=dict(settings.scorers.get(ref, {})))
            assess(self._store, self._registry.get(ref), scorer_ctx, trial=trial_dir.name)
        parts = store_key(
            self._registry.get(CODE_QUALITY.ref),
            settings.scorers.get(CODE_QUALITY.ref, {}),
            context_inputs(ctx),
        )
        self._store.put(
            Assessment(
                key=parts.key,
                scorer=parts.identity,
                config=parts.config,
                tool_lock=parts.tool_lock,
                inputs=parts.inputs,
                result=quality_result(ctx, plan),
            )
        )
