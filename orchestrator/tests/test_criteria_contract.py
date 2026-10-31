"""``criteria/1``: the strict structured-output contract, validated without any package."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from raidar_contract.criteria import CriteriaError, validate_criteria


def _criterion(identifier: str = "C1") -> dict[str, Any]:
    probe = {"situation": "a user asks", "outcome": "it works"}
    return {
        "id": identifier,
        "criterion": "It works.",
        "verification": "Run it.",
        "sources": ["quoted task text"],
        "kind": "new",
        "aspects": [probe],
        "examples": [probe],
    }


def _document() -> dict[str, Any]:
    feature = {"id": "F1", "name": "Feature", "outcome": "Done.", "criteria": [_criterion()]}
    return {"contract": "criteria/1", "features": [feature]}


def _mutated(change: Any) -> dict[str, Any]:
    document = copy.deepcopy(_document())
    change(document)
    return document


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(contract="criteria/2"),
        lambda d: d.update(extra=1),
        lambda d: d.update(features=[]),
        lambda d: d["features"][0].update(criteria=[]),
        lambda d: d["features"][0].update(id=""),
        lambda d: d["features"][0]["criteria"][0].update(kind="other"),
        lambda d: d["features"][0]["criteria"][0].update(aspects=[]),
        lambda d: d["features"][0]["criteria"][0].update(surprise=True),
        lambda d: d["features"][0]["criteria"][0].update(sources="not a list"),
        lambda d: d["features"][0]["criteria"][0]["examples"].append({"situation": "x"}),
        lambda d: d["features"][0]["criteria"].append(_criterion("C1")),
        lambda d: d["features"][0]["criteria"].append(_criterion("F1")),
    ],
)
def test_malformed_documents_are_refused(change: Any) -> None:
    with pytest.raises(CriteriaError):
        validate_criteria(_mutated(change))


def test_non_object_is_refused() -> None:
    with pytest.raises(CriteriaError):
        validate_criteria([1])


def test_criterion_ids_cannot_be_reused_by_another_feature() -> None:
    document = _document()
    document["features"].append(
        {"id": "F2", "name": "Other", "outcome": "Another outcome.", "criteria": [_criterion()]}
    )
    with pytest.raises(CriteriaError, match="duplicate ids"):
        validate_criteria(document)
