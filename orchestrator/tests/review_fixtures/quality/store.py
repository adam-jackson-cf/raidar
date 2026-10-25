"""Store declared synthetic Quality observations under real Assessment keys."""

from __future__ import annotations

from pathlib import Path

from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.framework.keys import scorer_identity, store_key
from raidar.scoring.framework.rescore_config import RescoreConfig
from raidar.scoring.framework.result import AssessResult
from raidar.scoring.framework.store import Assessment
from raidar.scoring.framework.trial_inputs import context_inputs, load_trial_context


class FixtureAssessments:
    """Cache scorer identities while storing declared synthetic observations under real keys."""

    def __init__(self, settings: RescoreConfig) -> None:
        self.settings = settings
        self.registry = default_scorer_registry()
        self.identities = {ref: scorer_identity(self.registry.get(ref)) for ref in settings.scorers}

    def assessment(self, ref: str, trial: Path, result: AssessResult) -> Assessment:
        inputs = context_inputs(
            load_trial_context(trial, inventory_policy=self.settings.inventory_policy)
        )
        registration = self.registry.get(ref)
        parts = store_key(
            registration,
            self.settings.scorers.get(ref, {}),
            inputs,
            identity=self.identities.get(ref),
        )
        return Assessment(
            key=parts.key,
            scorer=parts.identity,
            config=parts.config,
            tool_lock=parts.tool_lock,
            inputs=parts.inputs,
            result=result,
        )
