"""Deterministic score-sensitivity evidence for the canonical weights.

Ported from ``slop-code-bench`` ``src/slop_code/metrics/scoring/sensitivity.py``
at ``25069ba``: matched-correctness fixtures that degrade one component, the
exact score loss of a component change, counterfactual weight ablations, and
Pearson correlations between problem-level component means (used to reject
redundant components). Weights come from ``scb-canonical@1`` via
``arithmetic.WEIGHTS``.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from itertools import combinations

from raidar.scorers.code_quality.arithmetic import (
    COMPONENT_NAMES,
    DECIMAL_CONTEXT_PRECISION,
    WEIGHTS,
    calculate_problem_score,
)
from raidar.scorers.code_quality.models import (
    CheckpointCorrectnessEvidence,
    ProblemScore,
    canonical_decimal,
)

_TRANSITION_COMPONENTS = frozenset({"rework", "regression"})


class SensitivityError(ValueError):
    """Evidence cannot establish independent component effects."""


def controlled_fixture_scores(decrement: Decimal = Decimal("1")) -> dict[str, ProblemScore]:
    """Matched-correctness scores with exactly one degraded component each.

    The baseline and each perturbation use two fully correct checkpoints. V/E/H
    are checkpoint components and R/G transition components, so every
    component mean changes by exactly ``decrement``.
    """
    if decrement <= 0 or decrement > 1:
        raise SensitivityError("decrement must be in (0, 1]")
    correctness = tuple(
        CheckpointCorrectnessEvidence(
            checkpoint_id=f"checkpoint-{index}", passed=1, total=1, rate=Decimal("1")
        )
        for index in range(2)
    )
    values = {
        name: (Decimal("1"),) * (1 if name in _TRANSITION_COMPONENTS else 2)
        for name in COMPONENT_NAMES
    }
    scores = {"baseline": calculate_problem_score("baseline", correctness, **values)}
    for name in COMPONENT_NAMES:
        changed = values.copy()
        changed[name] = tuple(Decimal("1") - decrement for _ in values[name])
        scores[name] = calculate_problem_score(name, correctness, **changed)
    return scores


def exact_score_delta(component: str, decrement: Decimal) -> Decimal:
    """The unquantized score loss for one matched component change."""
    if component not in COMPONENT_NAMES:
        raise SensitivityError(f"unknown component: {component}")
    if decrement < 0 or decrement > 1:
        raise SensitivityError("decrement must be in [0, 1]")
    return Decimal("30") * WEIGHTS[component] * decrement


def weight_ablations(
    controlled_scores: Iterable[ProblemScore],
    eligible_baseline_scores: Iterable[ProblemScore] = (),
) -> dict[str, Decimal]:
    """Counterfactual score losses if each weight were zero, order-independent."""
    scores = tuple(
        sorted((*controlled_scores, *eligible_baseline_scores), key=lambda score: score.problem_id)
    )
    if len({score.problem_id for score in scores}) != len(scores):
        raise SensitivityError("ablation score identities must be unique")
    return {
        name: sum(
            (
                Decimal("30")
                * score.components.correctness
                * WEIGHTS[name]
                * getattr(score.components, name)
                for score in scores
            ),
            Decimal("0"),
        )
        for name in COMPONENT_NAMES
    }


def _pearson(x: tuple[Decimal, ...], y: tuple[Decimal, ...]) -> Decimal:
    x_mean = sum(x) / Decimal(len(x))
    y_mean = sum(y) / Decimal(len(y))
    numerator = sum(((a - x_mean) * (b - y_mean) for a, b in zip(x, y, strict=True)), Decimal("0"))
    x_variance = sum(((a - x_mean) ** 2 for a in x), Decimal("0"))
    y_variance = sum(((b - y_mean) ** 2 for b in y), Decimal("0"))
    if not x_variance or not y_variance:
        raise SensitivityError("constant components cannot establish independence")
    return canonical_decimal(numerator / (x_variance * y_variance).sqrt())


def component_correlations(scores: Iterable[ProblemScore]) -> dict[tuple[str, str], Decimal]:
    """Pearson correlations of the persisted component means, by problem id."""
    ordered = tuple(sorted(scores, key=lambda score: score.problem_id))
    if len(ordered) < 2:
        raise SensitivityError("at least two scores are required for correlation")
    with localcontext() as context:
        context.prec = DECIMAL_CONTEXT_PRECISION
        context.rounding = ROUND_HALF_EVEN
        return {
            (left, right): _pearson(
                tuple(getattr(score.components, left) for score in ordered),
                tuple(getattr(score.components, right) for score in ordered),
            )
            for left, right in combinations(COMPONENT_NAMES, 2)
        }


def require_independent_components(scores: Iterable[ProblemScore]) -> None:
    """Reject redundant or unidentifiable components under perturbation."""
    try:
        correlations = component_correlations(scores)
    except SensitivityError as error:
        raise SensitivityError("functionally redundant or unidentifiable components") from error
    for pair, correlation in correlations.items():
        if abs(correlation) == Decimal("1"):
            raise SensitivityError(f"functionally redundant components: {pair}")
