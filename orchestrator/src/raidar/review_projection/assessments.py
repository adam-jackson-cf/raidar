"""The stored Assessments of one trajectory, found by the key ``raidar assess --rescore`` uses.

The key is ``hash(scorer identity, config, tool lock, run inputs)``, so the
review shows exactly the Assessment a rescore with the run's locked assessment
config would reuse. An Assessment made by another scorer implementation or
config is a different key and is not shown; the scorer is then reported as not
assessed, with the command that assesses it (including ``--config`` with the
run's ``assessment-config.yaml``, without which the key differs).

``raidar analyze`` recomputes the same key (``analysis.lookup``) over one store
and keeps only the results; the review keeps the Assessment itself (its key is
shown in the scorecard), searches several stores and says why a scorer has
none, so it has its own lookup over the same key function.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from raidar.scorers.catalog import ScorerRegistry, tool_lock_for
from raidar.scorers.rescore import scorer_identity
from raidar.scorers.rescore_config import RescoreConfig
from raidar.scorers.store import Assessment, AssessmentStore, ScorerIdentity, assessment_key
from raidar.scorers.trial_inputs import (
    BundleCache,
    TrialInputError,
    context_inputs,
    load_trial_context,
)
from raidar.spec.references import RegistryReferenceError


@dataclass(frozen=True)
class TrialAssessments:
    """Assessments found for one trajectory, and why any scorer has none."""

    found: Mapping[str, Assessment] = field(default_factory=dict)
    missing: Mapping[str, str] = field(default_factory=dict)

    def get(self, ref: str) -> Assessment | None:
        return self.found.get(ref)


def rescore_hint(trial_dir: Path, ref: str, config_path: Path | None) -> str:
    """The command that stores exactly the Assessment this lookup looks for."""
    config = f" --config {config_path}" if config_path is not None else ""
    return f"raidar assess --rescore {trial_dir} --scorer {ref}{config} --store <store>"


class AssessmentLookup:
    """Looks up a trial's Assessments across one or more stores."""

    def __init__(self, stores: Sequence[AssessmentStore], registry: ScorerRegistry) -> None:
        self._stores = tuple(stores)
        self._registry = registry
        self._bundles = BundleCache()
        self._identities: dict[str, ScorerIdentity] = {}

    def _identity(self, ref: str) -> ScorerIdentity:
        if ref not in self._identities:
            self._identities[ref] = scorer_identity(self._registry.get(ref))
        return self._identities[ref]

    def _stored(self, key: str) -> Assessment | None:
        for store in self._stores:
            assessment = store.get(key)
            if assessment is not None:
                return assessment
        return None

    def _key(self, ref: str, config: Mapping[str, object], inputs: Mapping[str, object]) -> str:
        registration = self._registry.get(ref)
        return assessment_key(
            self._identity(ref),
            config=dict(config),
            tool_lock=tool_lock_for(registration.manifest),
            inputs=inputs,
        )

    def for_trial(
        self,
        trial_dir: Path,
        scorer_refs: Sequence[str],
        settings: RescoreConfig,
        *,
        config_path: Path | None = None,
    ) -> TrialAssessments:
        """``settings`` is the run's assessment config, read from ``config_path`` if any."""
        if not self._stores:
            return TrialAssessments(
                missing=dict.fromkeys(scorer_refs, "no assessment store was given")
            )
        try:
            inputs = context_inputs(
                load_trial_context(
                    trial_dir,
                    inventory_policy=settings.inventory_policy,
                    bundle_cache=self._bundles,
                )
            )
        except TrialInputError as exc:
            reason = f"trial inputs cannot be assessed: {exc}"
            return TrialAssessments(missing=dict.fromkeys(scorer_refs, reason))
        found: dict[str, Assessment] = {}
        missing: dict[str, str] = {}
        for ref in scorer_refs:
            try:
                key = self._key(ref, settings.scorers.get(ref, {}), inputs)
            except RegistryReferenceError as exc:
                missing[ref] = f"scorer {ref} is not registered: {exc}"
                continue
            assessment = self._stored(key)
            if assessment is None:
                missing[ref] = (
                    f"no stored {ref} Assessment for these inputs (key {key[:12]}); assess it "
                    f"with `{rescore_hint(trial_dir, ref, config_path)}`"
                )
            else:
                found[ref] = assessment
        return TrialAssessments(found=found, missing=missing)
