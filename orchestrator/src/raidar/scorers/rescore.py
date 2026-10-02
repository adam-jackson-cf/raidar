"""Offline rescoring: stored trials × scorers → Assessments (plan F2).

``raidar assess --rescore`` runs here. Each (trial, scorer) pair is keyed
before anything runs. An existing key is reused unless ``replay`` is set, in
which case the scorer runs again and the store verifies the bytes are
identical. A provisional Assessment (some Jev decision had no answer yet) is
never reused: it runs again, and a complete result supersedes it. A new
scorer version (or config, tool lock or input) gives a new key, so rescoring
only ever adds Assessments (or completes a provisional one). Trial
directories are read, never written.

Scorers load through their store-aware factory when they have one, so
``process-compliance@1`` gets a ``JevRuntime`` whose audit and cache live in the
store (its repo policy mode, shadow by default; ``jev_replay_only`` never calls
the provider). A scorer's problem-derived config keys (``entry_file``) default
from ``AssessContext.problem_config``; explicit config wins. The key hashes the
explicit config only: the defaults are a function of the problem reference and
oracle records already in the key's inputs, and analysis recomputes keys from
the job's explicit assessment config.

Trial ids such as ``b01-p0-guided`` repeat across runs, so a trial inside a
WS-D job (``<job>/trials/<trial>`` with ``<job>/run.json``) is recorded as
its review id ``<experiment>.<job>.<trial>`` (``qualified_trial_id``; ``<job>/<trial>``
when ``job.json`` names no experiment), in the
store log and in every Jev decision the scorers audit. Calibration splits
over those ids are then disjoint across runs, and the Quality view attaches
the decisions to exactly one trial. A trial outside a job keeps its directory
name.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from raidar.scorers.catalog import ScorerRegistration, ScorerRegistry
from raidar.scorers.inventory import InventoryPolicy
from raidar.scorers.keys import store_key
from raidar.scorers.sdk.context import (
    AssessContext,
    AwaitingAnswersScorer,
    RawPayloadScorer,
    Scorer,
)
from raidar.scorers.sdk.result import AssessResult, RunStats
from raidar.scorers.sdk.validation import validate_result, validate_scorer_set
from raidar.scorers.store import Assessment, AssessmentStore, PutOutcome
from raidar.scorers.trial_inputs import (
    BundleCache,
    TrialInputError,
    context_inputs,
    load_trial_context,
)
from raidar.verifier.regrade import RegradeError, trial_dirs


class RescoreError(ValueError):
    """The rescore request cannot run (bad target, unknown scorer, invalid input)."""


_JOB_MANIFEST = "job.json"
# A trial dir sits at <job>/trials/<trial>, so its job.json is at most two levels up.
_JOB_DEPTH = 2
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


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
                run = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RescoreError(f"cannot read {manifest}: {exc}") from exc
            experiment = run.get("experiment")
            job_id = run.get("job_id") or candidate.name
            if not experiment:  # the Quality view also attaches ``<job>/<trial>``
                return f"{job_id}/{trial_dir.name}"
            return _UNSAFE_ID.sub("-", f"{experiment}.{job_id}.{trial_dir.name}")
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
    jev_replay_only: bool = False,
) -> AssessmentRecord:
    """Store (or reuse, or replay-verify) one scorer's Assessment of one trial."""
    parts = store_key(registration, ctx.config, context_inputs(ctx))
    identity, key = parts.identity, parts.key
    stored = None if replay else store.get(key)
    if stored is not None and not stored.provisional:
        return AssessmentRecord(
            trial, identity.ref, key, RescoreOutcome.EXISTING, store.path_for(key)
        )
    scorer = registration.load_for_store(store.root, replay_only=jev_replay_only, trial=trial)
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
    jev_replay_only: bool = False,
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
        trial_id = qualified_trial_id(trial_dir)
        for registration in registrations:
            scorer_config = dict((config or {}).get(registration.manifest.ref, {}))
            ctx = replace(loaded, config=scorer_config)
            records.append(
                assess(
                    store,
                    registration,
                    ctx,
                    trial=trial_id,
                    replay=replay,
                    jev_replay_only=jev_replay_only,
                )
            )
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
