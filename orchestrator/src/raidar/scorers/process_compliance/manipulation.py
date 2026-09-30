"""Manipulation check: did the guided arm's process actually differ from baseline? (G5)

A process-guidance condition only means something if agents under it behave
differently. This check takes ``process-compliance@1`` results per arm and
reports, per check, whether the guided arm complied more than the baseline.

The unit is the trajectory, not the checkpoint, because checkpoints of one
attempt are not independent: each trajectory contributes its pass share over
its decided checkpoints for that check. The test is a one-sided exact
permutation test on the difference of arm means (guided minus baseline),
enumerating every relabelling when there are at most ``EXACT_LIMIT`` and
otherwise drawing a fixed-seed sample, so the report is deterministic.

Verdicts per check:

- ``separated``: guided mean > baseline mean and p <= alpha;
- ``not_separated``: the data could have shown separation but did not;
- ``insufficient_data``: an arm has no trajectory with a decided check, or
  the arm sizes are too small for any data to reach alpha (``1 / C(n, k)``).

``min_p_value`` is the smallest p these data could give, counting ties (with
binary shares many relabellings tie with the most extreme one). When it is
above alpha the verdict is ``not_separated`` and the reason says ties, not
sample size, prevented separation.

The overall verdict is ``separated`` only when every requested check is.

Placement: this compares arms, which F6 keeps out of scorers; it is analysis
code that reads stored ``process-compliance@1`` results and lives beside the
scorer only until WS-J's ``analysis/`` package exists. No scorer imports it.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from itertools import combinations
from pathlib import Path

from pydantic import Field

from raidar.scorers.process_compliance.checks import PLAN_WRITTEN, TESTS_RUN_BEFORE_FINISH
from raidar.scorers.sdk.manifest import SdkModel
from raidar.scorers.sdk.result import AssessResult, AssessStatus
from raidar.scorers.store import AssessmentStore

DEFAULT_CHECKS = (TESTS_RUN_BEFORE_FINISH, PLAN_WRITTEN)
EXACT_LIMIT = 50_000
SAMPLES = 20_000
SEED = 0
PROCESS_COMPLIANCE_REF = "process-compliance@1"


class Separation(StrEnum):
    SEPARATED = "separated"
    NOT_SEPARATED = "not_separated"
    INSUFFICIENT_DATA = "insufficient_data"


class ArmSummary(SdkModel):
    arm: str
    trajectories: int = Field(ge=0)
    mean: float | None


class CheckSeparation(SdkModel):
    check: str
    guided: ArmSummary
    baseline: ArmSummary
    verdict: Separation
    reason: str
    difference: float | None = None
    p_value: float | None = None
    min_p_value: float | None = None
    method: str | None = None


class ManipulationReport(SdkModel):
    kind: str = "raidar-manipulation-check"
    schema_version: int = 1
    alpha: float
    checks: tuple[CheckSeparation, ...]
    verdict: Separation


def trajectory_share(result: AssessResult, check: str) -> float | None:
    """Pass share of ``check`` over one trajectory's decided checkpoints, or None."""
    values = [
        observation.value
        for observation in result.observations
        if observation.metric == check
        and observation.status is AssessStatus.OK
        and observation.value is not None
    ]
    return sum(values) / len(values) if values else None


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _splits(n: int, k: int) -> tuple[Iterable[tuple[int, ...]], int, str]:
    total = math.comb(n, k)
    if total <= EXACT_LIMIT:
        return combinations(range(n), k), total, "exact-permutation"
    rng = random.Random(SEED)
    return (tuple(rng.sample(range(n), k)) for _ in range(SAMPLES)), SAMPLES, "sampled-permutation"


def permutation_p(guided: Sequence[float], baseline: Sequence[float]) -> tuple[float, float, str]:
    """(one-sided p, smallest attainable p, method) for mean(guided) > mean(baseline)."""
    pooled = [*guided, *baseline]
    observed = _mean(guided) - _mean(baseline)
    splits, total, method = _splits(len(pooled), len(guided))
    total_sum = sum(pooled)
    extreme = 0
    for chosen in splits:
        chosen_sum = sum(pooled[index] for index in chosen)
        rest = (total_sum - chosen_sum) / len(baseline)
        if chosen_sum / len(guided) - rest >= observed - 1e-12:
            extreme += 1
    floor = _extreme_share(pooled, len(guided))
    if method == "sampled-permutation":
        return (extreme + 1) / (total + 1), max(floor, 1 / (total + 1)), method
    return extreme / total, floor, method


def _extreme_share(pooled: Sequence[float], k: int) -> float:
    """Share of relabellings as extreme as the most extreme one: the smallest attainable p.

    With ties (binary shares are all ties) many relabellings reach the maximum,
    so this is ``C(tied, needed) / C(n, k)``, not ``1 / C(n, k)``.
    """
    ordered = sorted(pooled, reverse=True)
    cut = ordered[k - 1]
    above = sum(1 for value in pooled if value > cut + 1e-12)
    tied = sum(1 for value in pooled if abs(value - cut) <= 1e-12)
    return math.comb(tied, k - above) / math.comb(len(pooled), k)


def _summary(arm: str, shares: Sequence[float]) -> ArmSummary:
    return ArmSummary(arm=arm, trajectories=len(shares), mean=_mean(shares) if shares else None)


def separate(
    check: str,
    guided: tuple[str, Sequence[float]],
    baseline: tuple[str, Sequence[float]],
    alpha: float,
) -> CheckSeparation:
    guided_summary, baseline_summary = _summary(*guided), _summary(*baseline)
    if not guided[1] or not baseline[1]:
        return CheckSeparation(
            check=check,
            guided=guided_summary,
            baseline=baseline_summary,
            verdict=Separation.INSUFFICIENT_DATA,
            reason="an arm has no trajectory with a decided check",
        )
    difference = _mean(guided[1]) - _mean(baseline[1])
    p_value, min_p, method = permutation_p(guided[1], baseline[1])
    design_p = 1 / math.comb(len(guided[1]) + len(baseline[1]), len(guided[1]))
    if design_p > alpha:
        verdict = Separation.INSUFFICIENT_DATA
        reason = f"arms too small: smallest p for any data {design_p:.4g} > alpha"
    elif min_p > alpha:
        verdict = Separation.NOT_SEPARATED
        reason = f"tied shares: smallest p for these data {min_p:.4g} > alpha"
    elif difference > 0 and p_value <= alpha:
        verdict, reason = Separation.SEPARATED, f"guided complies more (p={p_value:.4g})"
    else:
        verdict, reason = Separation.NOT_SEPARATED, f"no separation at alpha (p={p_value:.4g})"
    return CheckSeparation(
        check=check,
        guided=guided_summary,
        baseline=baseline_summary,
        difference=difference,
        p_value=p_value,
        min_p_value=min_p,
        method=method,
        verdict=verdict,
        reason=reason,
    )


def _overall(checks: Sequence[CheckSeparation]) -> Separation:
    verdicts = {check.verdict for check in checks}
    if verdicts == {Separation.SEPARATED}:
        return Separation.SEPARATED
    if Separation.NOT_SEPARATED in verdicts:
        return Separation.NOT_SEPARATED
    return Separation.INSUFFICIENT_DATA


def manipulation_check(
    results: Mapping[str, Sequence[AssessResult]],
    *,
    guided: str,
    baseline: str,
    checks: Sequence[str] = DEFAULT_CHECKS,
    alpha: float = 0.05,
) -> ManipulationReport:
    """Whether the guided arm's process-compliance results separate from the baseline's."""
    missing = sorted({guided, baseline} - set(results))
    if missing:
        raise ValueError(f"no process-compliance results for arms {missing}")
    if not checks:
        raise ValueError("choose at least one check")
    separations = []
    for check in checks:
        shares = {
            arm: [
                share
                for result in results[arm]
                if (share := trajectory_share(result, check)) is not None
            ]
            for arm in (guided, baseline)
        }
        separations.append(
            separate(check, (guided, shares[guided]), (baseline, shares[baseline]), alpha)
        )
    return ManipulationReport(alpha=alpha, checks=tuple(separations), verdict=_overall(separations))


def load_arm_results(
    store_root: Path, arm_keys: Mapping[str, Sequence[str]]
) -> dict[str, list[AssessResult]]:
    """``process-compliance@1`` results per arm from Assessment keys in a store."""
    store = AssessmentStore(store_root)
    loaded: dict[str, list[AssessResult]] = {}
    for arm, keys in arm_keys.items():
        loaded[arm] = []
        for key in keys:
            assessment = store.get(key)
            if assessment is None:
                raise ValueError(f"arm {arm}: no assessment {key} in {store_root}")
            if assessment.scorer.ref != PROCESS_COMPLIANCE_REF:
                raise ValueError(
                    f"arm {arm}: assessment {key} is {assessment.scorer.ref}, "
                    f"not {PROCESS_COMPLIANCE_REF}"
                )
            loaded[arm].append(assessment.result)
    return loaded
