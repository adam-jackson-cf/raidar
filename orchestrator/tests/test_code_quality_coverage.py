"""code-quality's G reads the offline coverage run of a deferred step and leaves out flipped cases.

A trial graded with ``protocol.verifier_coverage: offline`` has no in-trial
coverage pass (``record.coverage.enabled`` false, no ``g-kernel-input.json``).
The scorer reads the same parity verdict and G input from the Assessment store's
offline run, so its observations equal those over the same trial graded with the
in-trial pass; a run not produced yet is ``unavailable`` and the Assessment
provisional; a failed offline parity check is ``ineligible``. A step whose trial
made the pass is read from the trial.

G leaves out at most three cases whose outcome flipped between the grading run
and the coverage run, when nothing else about the two runs differs, and computes
G on the rest; it reports how many cases it left out per transition. Every other
parity failure stays ``ineligible`` (smoke-3b-4 I1).
"""

from __future__ import annotations

import copy
from typing import Any

from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.code_quality.lineage_models import RegressionRunEvidence
from raidar.scoring.code_quality.observations import EXCLUDED_METRIC
from raidar.scoring.code_quality.parity import (
    MAX_EXCLUDED_CASES,
    flipped_cases,
    reconcile,
)
from tests.code_quality_support import FLAKY, FLIPPED, flip_run, golden_case


def _runs(case: dict[str, Any], step: str) -> tuple[RegressionRunEvidence, RegressionRunEvidence]:
    runs = case["runs"][step]
    return (
        RegressionRunEvidence.model_validate(runs["baseline"]),
        RegressionRunEvidence.model_validate(runs["produced"]),
    )


def test_flipped_cases_names_only_outcome_flips() -> None:
    case = golden_case("synthetic_shop")
    baseline, produced = _runs(case, "checkpoint_2")
    flipped = RegressionRunEvidence.model_validate(
        flip_run(case["runs"]["checkpoint_2"]["produced"], FLAKY)
    )

    assert flipped_cases(baseline, produced) == ()
    assert flipped_cases(baseline, flipped) == (FLAKY,)
    # A case collected by only one run, a changed invocation or an inconsistent ledger is not
    # an outcome flip.
    fewer = flipped.model_copy(
        update={
            "outcomes": flipped.outcomes[1:],
            "ledger": flipped.ledger[1:],
        }
    )
    assert flipped_cases(baseline, fewer) is None
    moved = flipped.model_copy(
        update={"invocation": baseline.invocation.model_copy(update={"cwd": "/elsewhere"})}
    )
    assert flipped_cases(baseline, moved) is None
    broken = flipped.model_copy(update={"ledger": flipped.ledger[::-1]})
    assert flipped_cases(baseline, broken) is None


def test_reconcile_caps_the_cases_it_leaves_out() -> None:
    case = golden_case("synthetic_shop")
    baseline, _ = _runs(case, "checkpoint_2")
    nodes = tuple(item.node_id for item in baseline.outcomes)
    assert len(nodes) <= MAX_EXCLUDED_CASES
    produced = case["runs"]["checkpoint_2"]["produced"]
    for node in nodes:
        produced = flip_run(produced, node, "error")
    every = RegressionRunEvidence.model_validate(produced)
    one = RegressionRunEvidence.model_validate(
        flip_run(case["runs"]["checkpoint_2"]["produced"], FLAKY)
    )

    settled = reconcile(FLIPPED.reasons, baseline, one)
    assert settled is not None
    left, right, flipped = settled
    assert flipped == (FLAKY,)
    assert FLAKY not in {item.node_id for run in (left, right) for item in run.outcomes}
    assert FLAKY not in {item.node_id for item in right.coverage}
    assert reconcile((), baseline, one) is None
    assert reconcile(("environment_mismatch",), baseline, one) is None
    assert reconcile(FLIPPED.reasons, baseline, baseline) is None
    # Every case flipped: within the cap here (3 cases), so it is still settled.
    assert reconcile(FLIPPED.reasons, baseline, every) is not None


def test_reconcile_refuses_more_flips_than_the_cap() -> None:
    case = golden_case("synthetic_shop")
    baseline, _ = _runs(case, "checkpoint_2")
    extra = [
        {**baseline.outcomes[1].model_dump(mode="json"), "node_id": f"tests/test_x.py::test_{i}"}
        for i in range(MAX_EXCLUDED_CASES + 1)
    ]
    grading = case["runs"]["checkpoint_2"]["baseline"]
    wide_base = {
        **grading,
        "outcomes": [*grading["outcomes"], *extra],
        "ledger": [
            *grading["ledger"],
            *({key: row[key] for key in ("node_id", "phase", "outcome")} for row in extra),
        ],
    }
    wide_produced = copy.deepcopy(wide_base)
    wide_produced["coverage"] = case["runs"]["checkpoint_2"]["produced"]["coverage"]
    for row in extra:
        wide_produced = flip_run(wide_produced, row["node_id"])
    left = RegressionRunEvidence.model_validate(wide_base)
    right = RegressionRunEvidence.model_validate(wide_produced)

    assert len(flipped_cases(left, right) or ()) == MAX_EXCLUDED_CASES + 1
    assert reconcile(FLIPPED.reasons, left, right) is None


def test_code_quality_is_registered_store_bound_with_the_exclusion_count() -> None:
    registration = default_scorer_registry().get("code-quality@9")

    assert registration.store_implementation is not None
    assert registration.problem_config == ("entry_file",)
    assert EXCLUDED_METRIC in {metric.id for metric in registration.manifest.metrics}
