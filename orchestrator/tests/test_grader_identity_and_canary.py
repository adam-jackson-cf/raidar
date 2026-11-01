"""Grader identity admission, pinning and mismatch refusals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.design.problem.validation import (
    GraderIdentity,
    read_grader_identity,
    read_validation_record,
)
from raidar.engine.trial.plan import PlanError, _pinned_grader
from raidar.grading.grader_image import GraderIdentityError, refuse_grader_mismatch
from raidar.grading.records import grading_execution_digest

ENVIRONMENT = "sha256:" + "a" * 64
IMPLEMENTATION = "b" * 64
COMPLETE = {
    "grader_image": "raidar-oracle-grader:abc",
    "grader_environment_id": ENVIRONMENT,
    "grader_implementation_sha256": IMPLEMENTATION,
}


# ------------------------------------------------------------ recorded identity
def test_a_report_with_every_part_records_a_complete_identity() -> None:
    identity = read_grader_identity(COMPLETE)
    assert identity == GraderIdentity("raidar-oracle-grader:abc", ENVIRONMENT, IMPLEMENTATION)
    assert identity is not None
    assert identity.complete


@pytest.mark.parametrize(
    "implementation", [None, "", 7, [], "a" * 63, "a" * 65, "g" * 64, "A" * 64]
)
def test_an_absent_or_untrusted_implementation_leaves_identity_unmeasured(
    implementation: object,
) -> None:
    identity = read_grader_identity({**COMPLETE, "grader_implementation_sha256": implementation})
    assert identity == GraderIdentity("raidar-oracle-grader:abc", ENVIRONMENT, None)
    assert identity is not None
    assert not identity.complete
    assert identity.lock()["implementation_sha256"] is None


@pytest.mark.parametrize(
    "environment",
    [
        None,
        {},
        {"grader_image": "g:1"},
        {**COMPLETE, "grader_image": ""},
        {**COMPLETE, "grader_image": 7},
        {**COMPLETE, "grader_environment_id": "g:1"},
        {**COMPLETE, "grader_environment_id": 7},
        {**COMPLETE, "grader_environment_id": "sha256:" + "a" * 63},
        {**COMPLETE, "grader_environment_id": "sha256:" + "g" * 64},
    ],
)
def test_a_report_without_an_immutable_environment_id_pins_no_grader(environment: object) -> None:
    assert read_grader_identity(environment) is None


def test_the_lock_pins_the_validated_grader_identity(tmp_path: Path) -> None:
    base = {"verdict": "validated", "manifest_sha256": "a" * 64, "not_run": []}
    path = tmp_path / "r.json"
    path.write_text(json.dumps({**base, "environment": COMPLETE}))
    pinned = read_validation_record(path).lock_metadata()["grader"]
    assert pinned == {
        "image": "raidar-oracle-grader:abc",
        "environment_id": ENVIRONMENT,
        "implementation_sha256": IMPLEMENTATION,
    }
    path.write_text(json.dumps(base))
    assert read_validation_record(path).lock_metadata()["grader"] is None


# ------------------------------------------------------------------ run refusal
def _references(grader: object) -> dict[str, object]:
    return {"fixture/adder@1": {"metadata": {"validation": {"grader": grader}}}}


def test_a_run_is_refused_when_the_lock_pins_no_complete_grader_identity() -> None:
    complete = {
        "image": "g:1",
        "environment_id": ENVIRONMENT,
        "implementation_sha256": grading_execution_digest(),
    }
    for missing in (None, {**complete, "implementation_sha256": None}, {"image": "g:1"}):
        with pytest.raises(PlanError, match="no complete grader identity"):
            _pinned_grader(0, "fixture/adder@1", _references(missing))
    assert _pinned_grader(0, "fixture/adder@1", _references(complete)) == complete


def test_a_run_is_refused_when_this_checkout_grades_with_another_implementation() -> None:
    other = {"image": "g:1", "environment_id": ENVIRONMENT, "implementation_sha256": "c" * 64}
    with pytest.raises(PlanError, match="validated under grading implementation"):
        _pinned_grader(0, "fixture/adder@1", _references(other))


def test_grading_refuses_a_grader_other_than_the_validated_one() -> None:
    pinned = {
        "image": "g:1",
        "environment_id": ENVIRONMENT,
        "implementation_sha256": IMPLEMENTATION,
    }
    refuse_grader_mismatch(
        pinned, image="g:1", environment_id=ENVIRONMENT, implementation_sha256=IMPLEMENTATION
    )
    for change in (
        {"image": "g:2"},
        {"environment_id": "sha256:" + "d" * 64},
        {"implementation_sha256": "e" * 64},
    ):
        found = {
            "image": "g:1",
            "environment_id": ENVIRONMENT,
            "implementation_sha256": IMPLEMENTATION,
            **change,
        }
        with pytest.raises(GraderIdentityError, match=next(iter(change))):
            refuse_grader_mismatch(pinned, **found)
