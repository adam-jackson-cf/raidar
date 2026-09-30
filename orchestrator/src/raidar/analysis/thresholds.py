"""Verdict thresholds from the lock (memo §2.3: fixed before any data is seen).

The lock's ``spec.analysis`` block pins ``min_pairs``, ``max_exclusion_fraction``,
``alpha`` and ``min_effects`` (``raidar.spec.schema.VERDICT_THRESHOLDS``). A lock
written before the schema carried them is *not pinned*: the documented
defaults fill the missing values and the report says so. A caller-supplied override (the
``raidar analyze`` options) replaces a pinned value and is named in
``Settings.overridden``, so a post-hoc change is always labelled.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from raidar.analysis.effects import Settings
from raidar.analysis.outcomes import outcome_metric
from raidar.analysis.stats import BOOTSTRAP_SAMPLES
from raidar.spec.schema import (
    DEFAULT_ALPHA,
    DEFAULT_MAX_EXCLUSION_FRACTION,
    DEFAULT_MIN_PAIRS,
    VERDICT_THRESHOLDS,
)

LOCK_SOURCE = "lock"
DEFAULT_SOURCE = "defaults (lock does not pin thresholds)"
OVERRIDE_SOURCE = "command line"


def _source(pinned: bool, overridden: tuple[str, ...]) -> str:
    base = LOCK_SOURCE if pinned else DEFAULT_SOURCE
    if not overridden:
        return base
    return f"{base}; {OVERRIDE_SOURCE} overrides {', '.join(overridden)}"


def _min_effects(raw: object) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    effects = {str(name): float(value) for name, value in raw.items()}
    for name in effects:
        outcome_metric(name)
    return effects


def settings_from_lock(
    analysis: Mapping[str, Any] | None,
    *,
    overrides: Mapping[str, Any] | None = None,
    bootstrap_samples: int = BOOTSTRAP_SAMPLES,
) -> Settings:
    """``Settings`` from a lock's ``spec.analysis`` block plus labelled overrides.

    ``overrides`` keys are ``min_pairs``, ``max_exclusion_fraction``, ``alpha``
    (a value of ``None`` means "not given") and ``min_effects`` (merged per
    metric over the lock's). Raises ``ValueError`` for an unknown metric or an
    out-of-range value.
    """
    block = dict(analysis or {})
    pinned = all(name in block for name in VERDICT_THRESHOLDS)
    values: dict[str, Any] = {
        "min_pairs": DEFAULT_MIN_PAIRS,
        "max_exclusion_fraction": DEFAULT_MAX_EXCLUSION_FRACTION,
        "alpha": DEFAULT_ALPHA,
        "min_effects": {},
    }
    values.update({name: block[name] for name in VERDICT_THRESHOLDS if name in block})
    values["min_effects"] = _min_effects(values["min_effects"])
    overridden: list[str] = []
    for name, value in (overrides or {}).items():
        if name == "min_effects":
            for metric, delta in _min_effects(value).items():
                values["min_effects"][metric] = delta
                overridden.append(f"min_effect.{metric}")
        elif value is not None:
            values[name] = value
            overridden.append(name)
    return Settings(
        min_pairs=int(values["min_pairs"]),
        max_exclusion_fraction=float(values["max_exclusion_fraction"]),
        alpha=float(values["alpha"]),
        bootstrap_samples=bootstrap_samples,
        seed=int(block.get("seed", 0)),
        min_effects=values["min_effects"],
        source=_source(pinned, tuple(overridden)),
        pinned_by_lock=pinned,
        overridden=tuple(overridden),
    )
