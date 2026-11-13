"""Offline rescoring: stored trials × scorers → Assessments (plan F2).

``raidar assess --rescore`` runs here. Each (trial, scorer) pair is keyed
before anything runs. An existing key is reused unless ``replay`` is set, in
which case the scorer runs again and the store verifies the bytes are
identical. A provisional Assessment (an offline answer was not produced yet) is
never reused: it runs again, and a complete result supersedes it. A new
scorer version (or config, tool lock or input) gives a new key, so rescoring
only ever adds Assessments (or completes a provisional one). Trial
directories are read, never written.

Scorers load through their store-aware factory when they have one (the offline coverage
runs and pre-fix grades they read live in the store).
A scorer's problem-derived config keys (``entry_file``) default
from ``AssessContext.problem_config``; explicit config wins. The key hashes the
explicit config only: the defaults are a function of the problem reference and
oracle records already in the key's inputs, and analysis recomputes keys from
the job's explicit assessment config.

Trial ids such as ``b01-p0-guided`` repeat across jobs, so a trial inside a
job (``<job>/trials/<trial>`` with ``<job>/job.json``) is recorded as
its review id ``<experiment>.<job>.<trial>`` (``qualified_trial_id``; ``<job>/<trial>``
when ``job.json`` names no experiment) in the store log. A trial outside a job keeps its
directory name.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from raidar.contract.file_io import load_text
from raidar.grading.regrade import RegradeError, trial_dirs
from raidar.scoring.framework.context import (
    AssessContext,
    AwaitingAnswersScorer,
    RawPayloadScorer,
    Scorer,
)
from raidar.scoring.framework.inventory import InventoryPolicy
from raidar.scoring.framework.keys import scorer_identity, store_key
from raidar.scoring.framework.registry import ScorerRegistration, ScorerRegistry
from raidar.scoring.framework.result import AssessResult, RunStats
from raidar.scoring.framework.store import Assessment, AssessmentStore, PutOutcome, ScorerIdentity
from raidar.scoring.framework.trial_inputs import (
    BundleCache,
    TrialInputError,
    context_inputs,
    load_trial_context,
)
from raidar.scoring.framework.validation import validate_result, validate_scorer_set


class RescoreError(ValueError):
    """The rescore request cannot run (bad target, unknown scorer, invalid input)."""


_JOB_MANIFEST = "job.json"
# A trial dir sits at <job>/trials/<trial>, so its job.json is at most two levels up.
_JOB_DEPTH = 2
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def review_trial_id(manifest: Mapping[str, Any], job_name: str, trial_name: str) -> str:
    """The review id of a trial in the job ``manifest`` describes (``job_name`` if it has no id).

    ``<experiment>.<job>.<trial>``, or ``<job>/<trial>`` when the manifest names no experiment.
    """
    experiment = manifest.get("experiment")
    job_id = manifest.get("job_id") or job_name
    if not experiment:  # the Quality view also attaches ``<job>/<trial>``
        return f"{job_id}/{trial_name}"
    return _UNSAFE_ID.sub("-", f"{experiment}.{job_id}.{trial_name}")


def qualified_trial_id(trial_dir: Path) -> str:
    """``<experiment>.<job>.<trial>`` for a trial inside a run, else the directory name.

    A ``job.json`` that names no experiment gives ``<job>/<trial>``.
    The same id the Quality view gives the trial (``TrialSource.review_id``).
    """
    candidate = trial_dir.resolve().parent
    for _ in range(_JOB_DEPTH):
        manifest = candidate / _JOB_MANIFEST
        if manifest.is_file():
            try:
                run = json.loads(load_text(manifest, encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RescoreError(f"cannot read {manifest}: {exc}") from exc
            return review_trial_id(run, candidate.name, trial_dir.name)
        candidate = candidate.parent
    return trial_dir.name


class RescoreOutcome(StrEnum):
    ADDED = "added"
    EXISTING = "existing"
    REPLAYED_IDENTICAL = "replayed_identical"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class AssessmentRecord:
    trial: str
    scorer: str
    key: str
    outcome: RescoreOutcome
    path: Path


def effective_config(registration: ScorerRegistration, ctx: AssessContext) -> dict[str, Any]:
    """Problem-derived defaults for the scorer's declared keys, overlaid by explicit config."""
    defaults = {
        name: ctx.problem_config[name]
        for name in registration.problem_config
        if ctx.problem_config.get(name) is not None
    }
    return {**defaults, **dict(ctx.config)}


@dataclass(frozen=True)
class _Produced:
    result: AssessResult
    raw_payloads: dict[str, str]
    provisional: bool


def _run(scorer: Scorer, ctx: AssessContext, store: AssessmentStore) -> _Produced:
    raw: dict[str, str] = {}
    if isinstance(scorer, RawPayloadScorer):
        result, payloads = scorer.assess_with_raw(ctx)
        raw = {name: store.put_raw(data) for name, data in sorted(payloads.items())}
    else:
        result = scorer.assess(ctx)
    provisional = isinstance(scorer, AwaitingAnswersScorer) and scorer.awaiting_answers()
    return _Produced(result, raw, provisional)


def _outcome(put: PutOutcome, *, replay: bool) -> RescoreOutcome:
    if put is PutOutcome.ADDED:
        return RescoreOutcome.ADDED
    if put is PutOutcome.SUPERSEDED:
        return RescoreOutcome.SUPERSEDED
    # Identical bytes without --replay: a provisional Assessment still awaits answers.
    return RescoreOutcome.REPLAYED_IDENTICAL if replay else RescoreOutcome.EXISTING


def assess(
    store: AssessmentStore,
    registration: ScorerRegistration,
    ctx: AssessContext,
    *,
    trial: str,
    replay: bool = False,
    identity: ScorerIdentity | None = None,
) -> AssessmentRecord:
    """Store (or reuse, or replay-verify) one scorer's Assessment of one trial.

    ``identity`` lets a caller assessing many trials hash the implementation once.
    """
    parts = store_key(registration, ctx.config, context_inputs(ctx), identity=identity)
    identity, key = parts.identity, parts.key
    stored = None if replay else store.get(key)
    if stored is not None and not stored.provisional:
        return AssessmentRecord(
            trial, identity.ref, key, RescoreOutcome.EXISTING, store.path_for(key)
        )
    scorer = registration.load_for_store(store.root)
    scored = replace(ctx, config=effective_config(registration, ctx))
    started = time.perf_counter()
    produced = _run(scorer, scored, store)
    result = validate_result(registration.manifest, scored, produced.result)
    stats = RunStats(duration_s=time.perf_counter() - started)
    assessment = Assessment(
        key=key,
        scorer=identity,
        config=parts.config,
        tool_lock=parts.tool_lock,
        inputs=parts.inputs,
        result=result,
        raw_payloads=produced.raw_payloads,
        provisional=produced.provisional,
    )
    outcome = _outcome(store.put(assessment), replay=replay)
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


def assess_trials(
    store: AssessmentStore,
    registrations: Sequence[ScorerRegistration],
    trials: Iterable[tuple[str, AssessContext]],
    *,
    config: Mapping[str, Mapping[str, Any]] | None = None,
    replay: bool = False,
    identity_for: Callable[[ScorerRegistration], ScorerIdentity] = scorer_identity,
) -> list[AssessmentRecord]:
    """Assess each loaded trial with every scorer, each under its own scorer config.

    ``trials`` is consumed lazily: a trial is assessed before the next one is loaded. A scorer's
    identity (it hashes the implementation source) is derived once, when the first trial reaches
    it, so a run that fails to load its first trial never pays for it.
    """
    identities: dict[str, ScorerIdentity] = {}
    records: list[AssessmentRecord] = []
    for trial_id, loaded in trials:
        for registration in registrations:
            ref = registration.manifest.ref
            if ref not in identities:
                identities[ref] = identity_for(registration)
            ctx = replace(loaded, config=dict((config or {}).get(ref, {})))
            records.append(
                assess(
                    store,
                    registration,
                    ctx,
                    trial=trial_id,
                    replay=replay,
                    identity=identities[ref],
                )
            )
    return records


def _loaded_trials(
    trials: Sequence[Path],
    load: Callable[[Path], AssessContext],
    trial_id: Callable[[Path], str] = qualified_trial_id,
) -> Iterator[tuple[str, AssessContext]]:
    """Each trial's review id and context, loading one trial at a time."""
    for trial_dir in trials:
        try:
            loaded = load(trial_dir)
        except TrialInputError as exc:
            raise RescoreError(str(exc)) from exc
        yield trial_id(trial_dir), loaded


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
    bundles = BundleCache()
    loaded = _loaded_trials(
        trials,
        lambda trial_dir: load_trial_context(
            trial_dir,
            inventory_policy=inventory_policy,
            bundle_override=bundle_override,
            bundle_cache=bundles,
        ),
    )
    return assess_trials(store, registrations, loaded, config=config, replay=replay)


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
