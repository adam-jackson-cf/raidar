"""Problem-level component arithmetic of the SCB fork's canonical score.

Ported from ``slop-code-bench`` ``src/slop_code/metrics/scoring/schema.py``
(``_mean``, ``_unit``, ``_calculate_problem``) at ``25069ba``:

- each problem component is the plain mean of its per-checkpoint (V, E, H)
  or per-transition (R, G) values, in Decimal with precision 50 and
  ROUND_HALF_EVEN;
- ``I = .15V + .15E + .20H + .30R + .20G`` and ``S = 100·C·(.70 + .30·I)``.

Scorers observe and policies weigh (F6, F15): the inertia weights and the
score formula are taken from the ``scb-canonical@1`` ScorePolicy
(``raidar.spec.policies``), not restated here. ``calculate_problem_score``
composes the component means with that policy into the fork's
``ProblemScore`` so golden tests and sensitivity analysis use one code path.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from raidar.scorers.code_quality.models import (
    ZERO,
    CheckpointCorrectnessEvidence,
    ComponentScores,
    ProblemScore,
    canonical_decimal,
)
from raidar.spec.policies import SCB_CANONICAL

DECIMAL_CONTEXT_PRECISION = 50
COMPONENT_NAMES = ("verbosity", "erosion", "architecture", "rework", "regression")
POLICY_COMPONENT = {
    "correctness": "C",
    "verbosity": "V",
    "erosion": "E",
    "architecture": "H",
    "rework": "R",
    "regression": "G",
}
WEIGHTS = {name: SCB_CANONICAL.inertia_weights[POLICY_COMPONENT[name]] for name in COMPONENT_NAMES}


def mean(values: Sequence[Decimal]) -> Decimal:
    """Mean at precision 50 (the fork's ``_mean``); empty sequences are an error."""
    if not values:
        raise ValueError("cannot average empty sequence")
    with localcontext() as context:
        context.prec = DECIMAL_CONTEXT_PRECISION
        context.rounding = ROUND_HALF_EVEN
        return sum(values, ZERO) / Decimal(len(values))


def unit(value: Decimal, name: str) -> Decimal:
    if not value.is_finite() or value < ZERO or value > Decimal("1"):
        raise ValueError(f"{name} must be a finite value in [0, 1]")
    return value


def _check_counts(
    checkpoints: int,
    per_checkpoint: Sequence[Sequence[Decimal]],
    per_transition: Sequence[Sequence[Decimal]],
) -> None:
    if checkpoints < 2:
        raise ValueError("configured problem must have at least two checkpoints")
    if any(len(values) != checkpoints for values in per_checkpoint):
        raise ValueError("checkpoint component count must match configured checkpoints")
    if any(len(values) != checkpoints - 1 for values in per_transition):
        raise ValueError("transition component count must be one less than checkpoints")


def _means(series: dict[str, tuple[Decimal, ...]]) -> dict[str, Decimal]:
    for name, values in series.items():
        for value in values:
            unit(value, name)
    return {name: mean(values) for name, values in series.items()}


def calculate_problem_score(
    problem_id: str,
    checkpoint_correctness: Sequence[CheckpointCorrectnessEvidence],
    verbosity: Sequence[Decimal],
    erosion: Sequence[Decimal],
    architecture: Sequence[Decimal],
    rework: Sequence[Decimal],
    regression: Sequence[Decimal],
) -> ProblemScore:
    """One problem's components, inertia and score from complete ordered evidence."""
    count = len(checkpoint_correctness)
    _check_counts(count, (verbosity, erosion, architecture), (rework, regression))
    if len({item.checkpoint_id for item in checkpoint_correctness}) != count:
        raise ValueError("correctness checkpoint identifiers must be unique")
    means = _means(
        {
            "correctness": tuple(item.rate for item in checkpoint_correctness),
            "verbosity": tuple(verbosity),
            "erosion": tuple(erosion),
            "architecture": tuple(architecture),
            "rework": tuple(rework),
            "regression": tuple(regression),
        }
    )
    scored = SCB_CANONICAL.score(
        checkpoints=count,
        components={POLICY_COMPONENT[name]: value for name, value in means.items()},
    )
    if scored.score is None or scored.inertia is None:
        raise ValueError(f"{SCB_CANONICAL.ref} could not score: {scored.reasons}")
    return ProblemScore(
        problem_id=problem_id,
        configured_checkpoints=count,
        checkpoint_correctness=tuple(checkpoint_correctness),
        components=ComponentScores(
            **{name: canonical_decimal(value) for name, value in means.items()},
            inertia=scored.inertia,
        ),
        score=scored.score,
    )
