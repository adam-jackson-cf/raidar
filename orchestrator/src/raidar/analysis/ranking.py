"""The ranking view of a benchmark: agents ordered by one outcome metric (kind ``benchmark``).

A benchmark is one experiment whose arms differ only in the agent (one
condition), so the paired verdicts against the reference agent (the
baseline arm) come from the same engine as an ab-test. The ranking view adds,
per arm:

- the mean of the ``rank_by`` metric (default: the primary outcome) over the
  arm's valid trials with its 95% interval (paired t below 10 values,
  bootstrap from 10, as everywhere in analysis);
- ``pass@k`` for every ``k`` up to the experiment's attempts: per problem the
  unbiased estimator ``1 - C(n - c, k) / C(n, k)`` over the arm's ``n``
  measured trials, ``c`` of which solved the final checkpoint
  (``final_solved``), averaged over the problems with ``n >= k``;
- the mean cost, tokens, turns and agent seconds of its valid trials;
- when the lock declares ``gate-effort@1``, the arm's gate-effort means
  (rounds to clean, first-pass clean, unresolved and gate-error rates, blocking
  findings by family, fix-session cost): ``None`` for an arm without the gate.

Rows are ordered by the metric's declared direction; arms without a measured
value sort last. Invalid (infra-excluded) trials never count, and an
unmeasured value is never read as zero (F6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import comb

from raidar.analysis.effects import IntervalOut, Settings, interval_out
from raidar.analysis.outcomes import (
    FINAL_SOLVED,
    AnalysisModel,
    TrialOutcome,
    outcome_metric,
)
from raidar.analysis.stats import seed_for, summarise
from raidar.scorers.sdk.manifest import Direction

PASS_DEFINITION = "final_solved: the trial solved its problem's final checkpoint"


class PassAtK(AnalysisModel):
    k: int
    value: float | None
    problems: int
    """Problems with at least ``k`` measured trials (the average's denominator)."""


class RankingRow(AnalysisModel):
    rank: int
    arm: str
    n: int
    mean: float | None
    interval: IntervalOut | None
    pass_at_k: tuple[PassAtK, ...]
    cost_means: dict[str, float | None]
    gate_means: dict[str, float | None] | None = None


class Ranking(AnalysisModel):
    rank_by: str
    direction: str
    reference_arm: str
    pass_definition: str = PASS_DEFINITION
    rows: tuple[RankingRow, ...]


def pass_at_k(n: int, c: int, k: int) -> float | None:
    """Unbiased pass@k from ``n`` samples with ``c`` passes; None when ``n < k``."""
    if k < 1 or n < k:
        return None
    return 1.0 - comb(n - c, k) / comb(n, k)


def _passes_by_problem(valid: Sequence[TrialOutcome]) -> dict[str, tuple[int, int]]:
    counts: dict[str, tuple[int, int]] = {}
    for outcome in valid:
        value = outcome.metric(FINAL_SOLVED)
        if not value.measured or value.value is None:
            continue
        n, c = counts.get(outcome.problem, (0, 0))
        counts[outcome.problem] = (n + 1, c + (1 if value.value >= 1.0 else 0))
    return counts


def _pass_rows(valid: Sequence[TrialOutcome], attempts: int) -> tuple[PassAtK, ...]:
    counts = _passes_by_problem(valid)
    rows = []
    for k in range(1, attempts + 1):
        values = [v for n, c in counts.values() if (v := pass_at_k(n, c, k)) is not None]
        rows.append(
            PassAtK(k=k, value=sum(values) / len(values) if values else None, problems=len(values))
        )
    return tuple(rows)


def _gate_row(means: Mapping[str, float | None] | None) -> dict[str, float | None] | None:
    return dict(means) if means is not None else None


def _sort_key(row: RankingRow, direction: Direction) -> tuple[bool, float, str]:
    if row.mean is None:
        return (True, 0.0, row.arm)
    return (False, -row.mean if direction is Direction.HIGHER_BETTER else row.mean, row.arm)


def ranking(
    outcomes: Sequence[TrialOutcome],
    arms: Sequence[str],
    *,
    rank_by: str,
    reference_arm: str,
    attempts: int,
    settings: Settings,
    cost_means: Mapping[str, Mapping[str, float | None]],
    gate_means: Mapping[str, Mapping[str, float | None] | None] | None = None,
) -> Ranking:
    """The ranking view over every arm (reference included)."""
    metric = outcome_metric(rank_by)
    unranked = []
    for arm in arms:
        valid = [o for o in outcomes if o.arm == arm and not o.infra_error]
        values = [
            value.value
            for o in valid
            if (value := o.metric(rank_by)).measured and value.value is not None
        ]
        summary = summarise(
            values,
            seed=seed_for(settings.seed, f"rank:{arm}:{rank_by}"),
            bootstrap_samples=settings.bootstrap_samples,
            confidence=1.0 - settings.alpha,
        )
        unranked.append(
            RankingRow(
                rank=0,
                arm=arm,
                n=summary.n,
                mean=summary.mean,
                interval=interval_out(summary.interval),
                pass_at_k=_pass_rows(valid, attempts),
                cost_means=dict(cost_means.get(arm, {})),
                gate_means=_gate_row((gate_means or {}).get(arm)),
            )
        )
    ordered = sorted(unranked, key=lambda row: _sort_key(row, metric.direction))
    return Ranking(
        rank_by=rank_by,
        direction=metric.direction.value,
        reference_arm=reference_arm,
        rows=tuple(row.model_copy(update={"rank": index + 1}) for index, row in enumerate(ordered)),
    )
