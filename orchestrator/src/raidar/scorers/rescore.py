"""Offline rescoring: stored trials × scorers → Assessments (plan F2).

``raidar assess --rescore`` runs here. Each (trial, scorer) pair is keyed
before anything runs. An existing key is reused unless ``replay`` is set, in
which case the scorer runs again and the store verifies the bytes are
identical. A new scorer version (or config, tool lock or input) gives a new
key, so rescoring only ever adds Assessments. Trial directories are read,
never written.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from raidar.oracle.regrade import RegradeError, trial_dirs
from raidar.scorers.catalog import ScorerRegistration, ScorerRegistry, tool_lock_for
from raidar.scorers.inventory import InventoryPolicy
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.sdk.result import RunStats
from raidar.scorers.sdk.validation import validate_result, validate_scorer_set
from raidar.scorers.store import (
    Assessment,
    AssessmentStore,
    PutOutcome,
    ScorerIdentity,
    assessment_key,
)
from raidar.scorers.trial_inputs import (
    BundleCache,
    TrialInputError,
    context_inputs,
    load_trial_context,
)


class RescoreError(ValueError):
    """The rescore request cannot run (bad target, unknown scorer, invalid input)."""


class RescoreOutcome(StrEnum):
    ADDED = "added"
    EXISTING = "existing"
    REPLAYED_IDENTICAL = "replayed_identical"


@dataclass(frozen=True)
class AssessmentRecord:
    trial: str
    scorer: str
    key: str
    outcome: RescoreOutcome
    path: Path


def scorer_identity(registration: ScorerRegistration) -> ScorerIdentity:
    return ScorerIdentity(
        ref=registration.manifest.ref,
        manifest_digest=registration.manifest.digest(),
        implementation_digest=registration.implementation_digest(),
    )


def assess(
    store: AssessmentStore,
    registration: ScorerRegistration,
    ctx: AssessContext,
    *,
    trial: str,
    replay: bool = False,
) -> AssessmentRecord:
    """Store (or reuse, or replay-verify) one scorer's Assessment of one trajectory."""
    identity = scorer_identity(registration)
    config = dict(ctx.config)
    tool_lock = tool_lock_for(registration.manifest)
    inputs = context_inputs(ctx)
    key = assessment_key(identity, config=config, tool_lock=tool_lock, inputs=inputs)
    if store.has(key) and not replay:
        return AssessmentRecord(
            trial, identity.ref, key, RescoreOutcome.EXISTING, store.path_for(key)
        )
    scorer = registration.load()
    started = time.perf_counter()
    result = validate_result(registration.manifest, ctx, scorer.assess(ctx))
    stats = RunStats(duration_s=time.perf_counter() - started)
    assessment = Assessment(
        key=key,
        scorer=identity,
        config=config,
        tool_lock=tool_lock,
        inputs=inputs,
        result=result,
    )
    put = store.put(assessment)
    outcome = RescoreOutcome.ADDED if put is PutOutcome.ADDED else RescoreOutcome.REPLAYED_IDENTICAL
    store.log(trial=trial, key=key, scorer=identity.ref, outcome=outcome.value, stats=stats)
    return AssessmentRecord(trial, identity.ref, key, outcome, store.path_for(key))


def _registrations(
    scorer_refs: Sequence[str], registry: ScorerRegistry, config: Mapping[str, object]
) -> list[ScorerRegistration]:
    if not scorer_refs:
        raise RescoreError("choose at least one scorer to rescore with")
    unused = sorted(set(config) - set(scorer_refs))
    if unused:
        raise RescoreError(f"config names scorers that are not being run: {unused}")
    registrations = [registry.get(ref) for ref in scorer_refs]
    validate_scorer_set(registration.manifest for registration in registrations)
    return registrations


def rescore_path(
    target: Path,
    scorer_refs: Sequence[str],
    store: AssessmentStore,
    registry: ScorerRegistry,
    *,
    config: Mapping[str, Mapping[str, Any]] | None = None,
    inventory_policy: InventoryPolicy | None = None,
    bundle_override: Path | None = None,
    replay: bool = False,
) -> list[AssessmentRecord]:
    """Assess every trial under ``target`` (a trial or job dir) with each scorer."""
    registrations = _registrations(scorer_refs, registry, config or {})
    try:
        trials = trial_dirs(target)
    except RegradeError as exc:
        raise RescoreError(str(exc)) from exc
    records: list[AssessmentRecord] = []
    bundles = BundleCache()
    for trial_dir in trials:
        try:
            loaded = load_trial_context(
                trial_dir,
                inventory_policy=inventory_policy,
                bundle_override=bundle_override,
                bundle_cache=bundles,
            )
        except TrialInputError as exc:
            raise RescoreError(str(exc)) from exc
        for registration in registrations:
            scorer_config = dict((config or {}).get(registration.manifest.ref, {}))
            ctx = replace(loaded, config=scorer_config)
            records.append(assess(store, registration, ctx, trial=trial_dir.name, replay=replay))
    return records


def report_payload(records: Sequence[AssessmentRecord]) -> dict[str, object]:
    counts = {outcome.value: 0 for outcome in RescoreOutcome}
    for record in records:
        counts[record.outcome.value] += 1
    return {
        "counts": counts,
        "assessments": [
            {
                "trial": record.trial,
                "scorer": record.scorer,
                "key": record.key,
                "outcome": record.outcome.value,
                "path": str(record.path),
            }
            for record in records
        ],
    }
