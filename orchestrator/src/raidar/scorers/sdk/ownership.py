"""Metric ownership rules shared by every scorer output path.

A scorer may emit only the metrics it owns, each metric exactly once, and the
set of emitted metrics must be complete. The scenario-epoch scorecard
(``runtime/scoring_outputs.py``) and the reboot Scorer SDK both enforce these
rules through the helpers here, so there is one definition of each rejection.
Keys are anything hashable whose ``str()`` names the metric.
"""

from __future__ import annotations

from collections.abc import Collection, Hashable, Iterable, Mapping, Sequence
from typing import TypeVar

KeyT = TypeVar("KeyT", bound=Hashable)
ValueT = TypeVar("ValueT")


class MetricOwnershipError(ValueError):
    """A scorer emitted a metric it does not own."""


class DuplicateMetricError(ValueError):
    """A metric was emitted more than once."""


class MissingMetricError(ValueError):
    """A required metric was not emitted."""


def reject_unowned(keys: Iterable[KeyT], owned: Collection[KeyT], *, owner: str) -> None:
    """Raise on the first emitted key that ``owner`` does not own."""
    for key in keys:
        if key not in owned:
            raise MetricOwnershipError(f"Scorer {owner} emitted unowned metric '{key}'")


def merge_unique(into: dict[KeyT, ValueT], items: Iterable[tuple[KeyT, ValueT]]) -> None:
    """Add ``items`` to ``into``; a key seen before (in either) is a duplicate."""
    for key, value in items:
        if key in into:
            raise DuplicateMetricError(f"Duplicate scorer metric output: {key}")
        into[key] = value


def ordered_complete(found: Mapping[KeyT, ValueT], required: Sequence[KeyT]) -> list[ValueT]:
    """Values in ``required`` order, only when every required key was emitted."""
    missing = [key for key in required if key not in found]
    if missing:
        raise MissingMetricError(
            "Scorers did not emit all resolved metrics: " + ", ".join(str(key) for key in missing)
        )
    return [found[key] for key in required]
