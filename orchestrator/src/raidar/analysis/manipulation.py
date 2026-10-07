"""Manipulation check: did the guided arm's process actually differ from baseline? (G5)

A process-guidance condition only means something if agents under it behave
differently. This check takes ``process-compliance`` results per arm (``@2``,
or the dormant ``@1`` for jobs that declare it) and reports, per check, whether
the guided arm complied more than the baseline. The checks are deterministic
trace checks: only ``ok`` observations count, so an undetermined check (and
``@1``'s parked Jev metric) never enters a share.

The unit is the trial, not the checkpoint, because checkpoints of one
step_result are not independent: each trial contributes its pass share over
its decided checkpoints for that check. The test is a one-sided exact
permutation test on the difference of arm means (guided minus baseline),
enumerating every relabelling when there are at most ``EXACT_LIMIT`` and
otherwise drawing a fixed-seed sample, so the report is deterministic.

Verdicts per check:

- ``separated``: guided mean > baseline mean and p <= alpha;
- ``not_separated``: the data could have shown separation but did not;
- ``insufficient_data``: an arm has no trial with a decided check, or
  the arm sizes are too small for any data to reach alpha (``1 / C(n, k)``).

``min_p_value`` is the smallest p these data could give, counting ties (with
binary shares many relabellings tie with the most extreme one). When it is
above alpha the verdict is ``not_separated`` and the reason says ties, not
sample size, prevented separation.

The overall verdict is ``separated`` only when every requested check is.

Available checks and the scorer each reads (``CHECK_SCORERS``):
``tests_run_before_finish`` and ``plan_written`` (``process-compliance``), and
``gate_clean_at_step_end`` (``gate-effort@1`` or ``@2``: the step's last
commit-gate run passed; a shadow gate never decides it, so a baseline arm with a
shadow gate has no decided checkpoint for it). The checks are the experiment's pre-registered
``analysis.manipulation_checks`` when its lock declares them; otherwise the
process-compliance defaults when that scorer is declared. A check may
pre-register ``min_share``: the share of the treatment arm's decided
checkpoints (pooled over its trials) that must pass. When the baseline arm has
no decided checkpoint for the check (the condition is absent there, e.g. no
commit gate), the threshold alone decides (``basis: threshold``): ``separated``
when the share reaches ``min_share``. When both arms have decided checkpoints
the check must also separate (``basis: threshold+separation``).

Placement: this compares arms, which F6 keeps out of scorers, so it is
analysis code over stored ``process-compliance`` and ``gate-effort`` results.
``raidar analyze`` runs it for every treatment arm against the baseline arm.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from itertools import combinations
from pathlib import Path
from typing import Literal

from pydantic import Field

from raidar.scorers.process_checks.checks import PLAN_WRITTEN, TESTS_RUN_BEFORE_FINISH
from raidar.scorers.sdk.manifest import SdkModel
from raidar.scorers.sdk.result import AssessResult, AssessStatus
from raidar.scorers.store import AssessmentStore

DEFAULT_CHECKS = (TESTS_RUN_BEFORE_FINISH, PLAN_WRITTEN)
EXACT_LIMIT = 50_000
SAMPLES = 20_000
SEED = 0
PROCESS_COMPLIANCE_REFS = (
    "process-compliance@6",
    "process-compliance@5",
    "process-compliance@4",
    "process-compliance@3",
    "process-compliance@2",
    "process-compliance@1",
)
GATE_EFFORT_REFS = (
    "gate-effort@9",
    "gate-effort@8",
    "gate-effort@7",
    "gate-effort@6",
    "gate-effort@5",
    "gate-effort@4",
    "gate-effort@3",
    "gate-effort@2",
    "gate-effort@1",
)
GATE_CLEAN = "gate_clean_at_step_end"
PROCESS_SOURCE = "process-compliance"
GATE_SOURCE = "gate-effort"
CHECK_SCORERS: dict[str, tuple[str, tuple[str, ...]]] = {
    TESTS_RUN_BEFORE_FINISH: (PROCESS_SOURCE, PROCESS_COMPLIANCE_REFS),
    PLAN_WRITTEN: (PROCESS_SOURCE, PROCESS_COMPLIANCE_REFS),
    GATE_CLEAN: (GATE_SOURCE, GATE_EFFORT_REFS),
}
"""``{check: (result source, scorer refs that report it)}``."""
Basis = Literal["separation", "threshold", "threshold+separation"]


class Separation(StrEnum):
    SEPARATED = "separated"
    NOT_SEPARATED = "not_separated"
    INSUFFICIENT_DATA = "insufficient_data"


class ArmSummary(SdkModel):
    arm: str
    trials: int = Field(ge=0)
    mean: float | None


class CheckRequest(SdkModel):
    """One pre-registered check; ``min_share`` adds a treatment-share threshold."""

    check: str
    min_share: float | None = Field(default=None, gt=0.0, le=1.0)


class StepShare(SdkModel):
    """Passed / decided checkpoints of one arm, pooled over its trials."""

    passed: int = Field(ge=0)
    decided: int = Field(ge=0)
    share: float | None


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
    basis: Basis = "separation"
    min_share: float | None = None
    guided_steps: StepShare | None = None


class ManipulationReport(SdkModel):
    kind: str = "raidar-manipulation-check"
    schema_version: int = 2
    alpha: float
    checks: tuple[CheckSeparation, ...]
    verdict: Separation
    preregistered: bool = False
    """True when the checks are the lock's ``analysis.manipulation_checks``."""


def _decided(result: AssessResult, check: str) -> list[float]:
    return [
        observation.value
        for observation in result.observations
        if observation.metric == check
        and observation.status is AssessStatus.OK
        and observation.value is not None
    ]


def trial_share(result: AssessResult, check: str) -> float | None:
    """Pass share of ``check`` over one trial's decided checkpoints, or None."""
    values = _decided(result, check)
    return sum(values) / len(values) if values else None


def step_share(results: Sequence[AssessResult], check: str) -> StepShare:
    """``check`` over every decided checkpoint of ``results`` (one arm), pooled."""
    values = [value for result in results for value in _decided(result, check)]
    passed = sum(1 for value in values if value >= 0.5)
    return StepShare(
        passed=passed, decided=len(values), share=passed / len(values) if values else None
    )


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
    return ArmSummary(arm=arm, trials=len(shares), mean=_mean(shares) if shares else None)


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
            reason="an arm has no trial with a decided check",
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


def _threshold(
    separation: CheckSeparation, min_share: float, guided: Sequence[AssessResult]
) -> CheckSeparation:
    """Apply ``min_share`` to the guided arm's pooled checkpoint share."""
    pooled = step_share(guided, separation.check)
    update: dict[str, object] = {"min_share": min_share, "guided_steps": pooled}
    if pooled.share is None:
        reason = "the treatment arm has no decided checkpoint"
        return separation.model_copy(
            update=update | {"verdict": Separation.INSUFFICIENT_DATA, "reason": reason}
        )
    met = pooled.share >= min_share - 1e-12
    share = f"{pooled.passed}/{pooled.decided} = {pooled.share:.4g}"
    sign = ">=" if met else "<"
    if separation.baseline.trials == 0:
        verdict = Separation.SEPARATED if met else Separation.NOT_SEPARATED
        reason = (
            f"treatment share {share} {sign} pre-registered {min_share:g}; "
            "the baseline has no decided checkpoint (the condition is absent there)"
        )
        return separation.model_copy(
            update=update | {"basis": "threshold", "verdict": verdict, "reason": reason}
        )
    verdict = separation.verdict if met else Separation.NOT_SEPARATED
    reason = f"treatment share {share} {sign} {min_share:g}; {separation.reason}"
    return separation.model_copy(
        update=update | {"basis": "threshold+separation", "verdict": verdict, "reason": reason}
    )


def _check(
    request: CheckRequest,
    results: Mapping[str, Sequence[AssessResult]],
    guided: str,
    baseline: str,
    alpha: float,
) -> CheckSeparation:
    shares = {
        arm: [
            share
            for result in results.get(arm, ())
            if (share := trial_share(result, request.check)) is not None
        ]
        for arm in (guided, baseline)
    }
    separation = separate(
        request.check, (guided, shares[guided]), (baseline, shares[baseline]), alpha
    )
    if request.min_share is None:
        return separation
    return _threshold(separation, request.min_share, results.get(guided, ()))


def _requests(checks: Sequence[str | CheckRequest]) -> list[CheckRequest]:
    if not checks:
        raise ValueError("choose at least one check")
    return [item if isinstance(item, CheckRequest) else CheckRequest(check=item) for item in checks]


def check_report(
    results: Mapping[str, Mapping[str, Sequence[AssessResult]]],
    checks: Sequence[str | CheckRequest],
    *,
    guided: str,
    baseline: str,
    alpha: float = 0.05,
    preregistered: bool = False,
) -> ManipulationReport:
    """Every requested check, each over the results of its own scorer.

    ``results`` maps a check name to ``{arm: [AssessResult, ...]}`` of the
    scorer that reports it (``CHECK_SCORERS``); an arm without results for a
    check reads as having no decided checkpoint.
    """
    separations = [
        _check(request, results.get(request.check, {}), guided, baseline, alpha)
        for request in _requests(checks)
    ]
    return ManipulationReport(
        alpha=alpha,
        checks=tuple(separations),
        verdict=_overall(separations),
        preregistered=preregistered,
    )


def manipulation_check(
    results: Mapping[str, Sequence[AssessResult]],
    *,
    guided: str,
    baseline: str,
    checks: Sequence[str | CheckRequest] = DEFAULT_CHECKS,
    alpha: float = 0.05,
) -> ManipulationReport:
    """Whether the guided arm's results (one scorer, every check) separate from the baseline's."""
    missing = sorted({guided, baseline} - set(results))
    if missing:
        raise ValueError(f"no process-compliance results for arms {missing}")
    requests = _requests(checks)
    return check_report(
        {request.check: results for request in requests},
        requests,
        guided=guided,
        baseline=baseline,
        alpha=alpha,
    )


def load_arm_results(
    store_root: Path, arm_keys: Mapping[str, Sequence[str]]
) -> dict[str, list[AssessResult]]:
    """``process-compliance`` (``@2`` or ``@1``) results per arm from Assessment keys."""
    store = AssessmentStore(store_root)
    loaded: dict[str, list[AssessResult]] = {}
    for arm, keys in arm_keys.items():
        loaded[arm] = []
        for key in keys:
            assessment = store.get(key)
            if assessment is None:
                raise ValueError(f"arm {arm}: no assessment {key} in {store_root}")
            if assessment.scorer.ref not in PROCESS_COMPLIANCE_REFS:
                raise ValueError(
                    f"arm {arm}: assessment {key} is {assessment.scorer.ref}, "
                    f"not one of {', '.join(PROCESS_COMPLIANCE_REFS)}"
                )
            loaded[arm].append(assessment.result)
    return loaded
