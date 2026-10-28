"""``criteria/1``: the structured acceptance-criteria output a stage package may declare.

Raidar validates the data itself, without importing any package. ``acceptance-reply/1`` and
``jev-judge/1`` are Raidar-owned consumers of the same shape.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CRITERIA_CONTRACT = "criteria/1"


class CriteriaError(ValueError):
    """A criteria document does not satisfy ``criteria/1``."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Probe(_Strict):
    """An aspect or example: a situation and the outcome expected in it."""

    situation: str
    outcome: str


class Criterion(_Strict):
    id: str = Field(min_length=1)
    criterion: str
    verification: str
    sources: tuple[str, ...]
    kind: Literal["new", "regression"]
    aspects: tuple[Probe, ...]
    examples: tuple[Probe, ...]

    @model_validator(mode="after")
    def _new_has_aspect(self) -> Criterion:
        if self.kind == "new" and not self.aspects:
            raise ValueError(f"new criterion {self.id!r} needs at least one aspect")
        return self


class Feature(_Strict):
    id: str = Field(min_length=1)
    name: str
    outcome: str
    criteria: tuple[Criterion, ...] = Field(min_length=1)


class Criteria(_Strict):
    contract: Literal["criteria/1"]
    features: tuple[Feature, ...] = Field(min_length=1)

    def all_criteria(self) -> Iterator[Criterion]:
        for feature in self.features:
            yield from feature.criteria

    @model_validator(mode="after")
    def _ids_unique(self) -> Criteria:
        ids = [item.id for item in self.features]
        ids += [item.id for item in self.all_criteria()]
        repeated = sorted(name for name, count in Counter(ids).items() if count > 1)
        if repeated:
            raise ValueError(f"duplicate ids {repeated}")
        return self


def validate_criteria(data: object) -> Criteria:
    """The parsed criteria of ``data``; ``CriteriaError`` names what is wrong."""
    try:
        return Criteria.model_validate(data)
    except ValidationError as error:
        raise CriteriaError(f"invalid {CRITERIA_CONTRACT} document: {error}") from error
