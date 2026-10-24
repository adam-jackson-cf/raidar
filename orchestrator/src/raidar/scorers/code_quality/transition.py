"""Rework (R) and regression breadth (G) for each adjacent checkpoint transition.

R runs the ported lineage kernel transition by transition, carrying the
modified-before ledger forward; a transition that cannot be measured is a gap
and the next one starts from an empty ledger (the fork's
``gap: reset_at_problem_boundary_and_missing_checkpoint``). As in the fork's
live producer, R needs V/E evidence at both ends of the transition, and its
lineage identities are V/E's unhashed eligible symbols (``diffs.rework_symbols``).

G reads the verifier's per-step fork-shaped run evidence
(``raidar-oracle/steps/<step>/g-kernel-input.json``): the graded run
is the baseline and the separate coverage run is the produced run. A step whose
trial made the verifier's coverage pass reads it through ``in_trial_run_evidence``; one
whose trial deferred it reads the store's offline run (``coverage_reader``). A failed
parity check or missing evidence makes the transition
``ineligible`` with typed reasons, never 0. That file's bytes are part of the
step's oracle record digest (``trial_inputs._record_digest``), so a trial whose
G input differs or is missing never reuses another trial's Assessment key.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from raidar.scorers.code_quality.checkpoint import CheckpointMeasure, Gap, Measured
from raidar.scorers.code_quality.diffs import changed_lines, regression_scope, rework_symbols
from raidar.scorers.code_quality.lineage import Ledger, calculate_transition_rework, next_ledger
from raidar.scorers.code_quality.lineage_models import (
    RegressionAttribution,
    RegressionBreadthInput,
    RegressionRunEvidence,
    SymbolIdentity,
    TransitionReworkEvidence,
)
from raidar.scorers.code_quality.regression import PARITY_UNAVAILABLE, calculate_regression_breadth
from raidar.scorers.sdk.context import StepInputs
from raidar.scorers.sdk.result import AssessStatus
from raidar.verifier.records import FORK_EVIDENCE_FILE, step_record_dir

PARITY_FAILED = "coverage_parity_failed"
PROVENANCE_UNAVAILABLE = "canonical_provenance_unavailable"
RunEvidences = tuple[RegressionRunEvidence, RegressionRunEvidence]


@dataclass(frozen=True)
class TransitionMeasure:
    prior: StepInputs
    current: StepInputs
    rework: TransitionReworkEvidence | Gap
    regression: RegressionAttribution | Gap
    roots: tuple[Path, Path] | None


def _named(measure: CheckpointMeasure, gap: Gap) -> Gap:
    return Gap(gap.status, f"{measure.step.step}: {gap.reason}")


def _pair(prior: CheckpointMeasure, current: CheckpointMeasure) -> tuple[Measured, Measured] | Gap:
    """Both endpoints' measurements, or the first endpoint's gap (named by step)."""
    first, second = prior.outcome, current.outcome
    if isinstance(first, Gap):
        return _named(prior, first)
    if isinstance(second, Gap):
        return _named(current, second)
    return first, second


def _lineage_symbols(
    pair: tuple[Measured, Measured], steps: tuple[str, str]
) -> tuple[tuple[SymbolIdentity, ...], tuple[SymbolIdentity, ...]] | Gap:
    """R's identities at both ends (V/E's eligible symbols), or which end lacks them."""
    prior, current = (side.quality for side in pair)
    if isinstance(prior, Gap) or isinstance(current, Gap):
        missing = [
            step
            for step, side in zip(steps, (prior, current), strict=True)
            if isinstance(side, Gap)
        ]
        return Gap(
            AssessStatus.INELIGIBLE,
            f"{PROVENANCE_UNAVAILABLE}: no V/E symbol evidence for {', '.join(missing)}",
        )
    return rework_symbols(prior.evidence), rework_symbols(current.evidence)


def _rework(
    pair: tuple[Measured, Measured] | Gap, steps: tuple[str, str], ledger: Ledger
) -> TransitionReworkEvidence | Gap:
    if isinstance(pair, Gap):
        return pair
    symbols = _lineage_symbols(pair, steps)
    if isinstance(symbols, Gap):
        return symbols
    try:
        removed, added = changed_lines(
            pair[0].snapshot, pair[1].snapshot, (symbol.path for side in symbols for symbol in side)
        )
    except (OSError, UnicodeError) as error:
        return Gap(AssessStatus.INELIGIBLE, f"{PROVENANCE_UNAVAILABLE}: {error}")
    return calculate_transition_rework(*symbols, removed, added, ledger)


def verifier_record_dir(step: StepInputs) -> Path | None:
    """The step's host-side oracle record directory, found from its stored snapshot."""
    if step.snapshot is None or step.record is None:
        return None
    depth = len(PurePosixPath(step.record.snapshot.stored_path).parts)
    return step_record_dir(step.snapshot.parents[depth - 1], step.step)


def in_trial_run_evidence(step: StepInputs) -> RunEvidences | Gap:
    """G's run evidence from the trial's own coverage pass (record parity verdict, fork input)."""
    record = step.record
    if record is None:
        return Gap(AssessStatus.UNAVAILABLE, f"{step.step}: the oracle did not grade this step")
    parity = record.coverage.parity
    if parity is None or not parity.eligible:
        detail = ", ".join(parity.reasons) if parity is not None else "no parity verdict"
        return Gap(AssessStatus.INELIGIBLE, f"{PARITY_FAILED}: {detail}")
    directory = verifier_record_dir(step)
    path = directory / FORK_EVIDENCE_FILE if directory is not None else None
    if path is None or not path.is_file():
        return Gap(AssessStatus.INELIGIBLE, f"{PARITY_UNAVAILABLE}: {FORK_EVIDENCE_FILE} missing")
    try:
        payload = json.loads(path.read_text())
        return (
            RegressionRunEvidence.model_validate(payload["baseline"]),
            RegressionRunEvidence.model_validate(payload["produced"]),
        )
    except (OSError, KeyError, TypeError, ValueError, ValidationError) as error:
        return Gap(AssessStatus.INELIGIBLE, f"{PARITY_UNAVAILABLE}: {error}")


def _regression(
    pair: tuple[Measured, Measured] | Gap,
    steps: tuple[StepInputs, StepInputs],
    rework: TransitionReworkEvidence | Gap,
    runs_for: Callable[[StepInputs], RunEvidences | Gap],
) -> RegressionAttribution | Gap:
    if isinstance(pair, Gap):
        return pair
    runs = runs_for(steps[1])
    if isinstance(runs, Gap):
        return runs
    prior, current = pair
    try:
        _, added = changed_lines(
            prior.snapshot,
            current.snapshot,
            (*prior.production_paths, *(symbol.path for symbol in current.symbols)),
        )
    except (OSError, UnicodeError) as error:
        return Gap(AssessStatus.INELIGIBLE, f"{PARITY_UNAVAILABLE}: {error}")
    candidates = rework.lineage_candidates if isinstance(rework, TransitionReworkEvidence) else ()
    attribution = calculate_regression_breadth(
        RegressionBreadthInput(
            transition_id=f"{steps[0].checkpoint}--{steps[1].checkpoint}",
            baseline=runs[0],
            produced=runs[1],
            changed_symbols=regression_scope(added, current.symbols, candidates),
        )
    )
    if attribution.regression is None:
        return Gap(AssessStatus.INELIGIBLE, ", ".join(attribution.eligibility.reasons))
    return attribution


def measure_transitions(
    measures: list[CheckpointMeasure], runs_for: Callable[[StepInputs], RunEvidences | Gap]
) -> list[TransitionMeasure]:
    """R and G for every adjacent pair, G's run evidence from ``runs_for``.

    R's ledger resets after any gap.
    """
    ledger: Ledger = {}
    transitions = []
    for prior, current in zip(measures, measures[1:], strict=False):
        pair = _pair(prior, current)
        rework = _rework(pair, (prior.step.step, current.step.step), ledger)
        ledger = next_ledger(rework) if isinstance(rework, TransitionReworkEvidence) else {}
        regression = _regression(pair, (prior.step, current.step), rework, runs_for)
        roots = None if isinstance(pair, Gap) else (pair[0].snapshot, pair[1].snapshot)
        transitions.append(TransitionMeasure(prior.step, current.step, rework, regression, roots))
    return transitions
