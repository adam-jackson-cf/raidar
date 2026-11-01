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
import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from raidar.grading.models import ParityVerdict
from raidar.scoring.code_quality import coverage_reader, parity
from raidar.scoring.code_quality.checkpoint import Gap
from raidar.scoring.code_quality.coverage_reader import OFFLINE_NOT_PRODUCED, CoverageReader
from raidar.scoring.code_quality.lineage_models import RegressionRunEvidence
from raidar.scoring.code_quality.parity import (
    MAX_EXCLUDED_CASES,
    flipped_cases,
    reconcile,
)
from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import AssessStatus
from tests.code_quality_support import FLAKY, FLIPPED, flip_run, golden_case, memory_step


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


_STEP = "checkpoint_2"


def _stored(parity: ParityVerdict, fork: dict[str, Any] | None) -> SimpleNamespace:
    return SimpleNamespace(parity=parity, fork=fork)


def _deferred_step(*, enabled: bool = False, parity: ParityVerdict | None = None) -> StepInputs:
    record = SimpleNamespace(
        step=_STEP,
        coverage=SimpleNamespace(enabled=enabled, parity=parity),
        bundle=SimpleNamespace(digest="bundle"),
        snapshot=SimpleNamespace(digest="snapshot"),
    )
    return dataclasses.replace(memory_step(_STEP, None), record=cast(Any, record))


@pytest.fixture(name="offline_store")
def _offline_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The Assessment store's offline coverage runs, by step (the read boundary is injected)."""
    runs: dict[str, Any] = {}

    def read(root: Path, bundle: str, step: str, snapshot: str, *, identity: str) -> Any:
        return runs.get(step)

    for module in (coverage_reader, parity):
        monkeypatch.setattr(module, "read_offline_coverage", read)
        monkeypatch.setattr(module, "coverage_identity", lambda record, directory: "identity")
    return runs


def test_an_unproduced_offline_run_is_unavailable_and_awaited(
    offline_store: dict[str, Any],
) -> None:
    for root in (Path("store"), None):
        reader = CoverageReader(root)

        gap = reader.run_evidence(_deferred_step())

        assert isinstance(gap, Gap)
        assert gap.status is AssessStatus.UNAVAILABLE
        assert gap.reason.startswith(OFFLINE_NOT_PRODUCED)
        assert reader.missing == [_STEP]
        assert reader.excluded == {}


def test_an_eligible_offline_run_is_read_whole(offline_store: dict[str, Any]) -> None:
    case = golden_case("synthetic_shop")
    offline_store[_STEP] = _stored(ParityVerdict(eligible=True), case["runs"][_STEP])
    reader = CoverageReader(Path("store"))

    found = reader.run_evidence(_deferred_step())

    assert found == _runs(case, _STEP)
    assert reader.excluded == {_STEP: ()}
    assert reader.missing == []


@pytest.mark.parametrize(
    ("fork", "reason"),
    [
        (None, "coverage_parity_unavailable"),
        ({}, "coverage_parity_unavailable"),
    ],
)
def test_an_eligible_offline_run_without_readable_g_input_is_ineligible(
    offline_store: dict[str, Any], fork: dict[str, Any] | None, reason: str
) -> None:
    offline_store[_STEP] = _stored(ParityVerdict(eligible=True), fork)

    gap = CoverageReader(Path("store")).run_evidence(_deferred_step())

    assert isinstance(gap, Gap)
    assert gap.status is AssessStatus.INELIGIBLE
    assert gap.reason.startswith(reason)


def test_a_failed_offline_parity_check_is_ineligible_unless_only_a_few_outcomes_flipped(
    offline_store: dict[str, Any],
) -> None:
    case = golden_case("synthetic_shop")
    flipped = {**case["runs"][_STEP], "produced": flip_run(case["runs"][_STEP]["produced"], FLAKY)}
    other = ParityVerdict(eligible=False, reasons=("environment_mismatch",))
    offline_store[_STEP] = _stored(other, flipped)
    reader = CoverageReader(Path("store"))

    gap = reader.run_evidence(_deferred_step())

    assert isinstance(gap, Gap)
    assert (gap.status, gap.reason.split(":")[0]) == (
        AssessStatus.INELIGIBLE,
        "coverage_parity_failed",
    )
    assert reader.excluded == {}

    offline_store[_STEP] = _stored(FLIPPED, flipped)
    settled = reader.run_evidence(_deferred_step())

    assert not isinstance(settled, Gap)
    assert reader.excluded == {_STEP: (FLAKY,)}
    assert FLAKY not in {item.node_id for run in settled for item in run.outcomes}

    offline_store[_STEP] = _stored(FLIPPED, {"baseline": "not a run"})
    assert isinstance(CoverageReader(Path("store")).run_evidence(_deferred_step()), Gap)


def test_a_step_the_oracle_did_not_grade_has_no_run_evidence(offline_store: dict[str, Any]) -> None:
    ungraded = dataclasses.replace(memory_step(_STEP, None), record=None)

    gap = CoverageReader(Path("store")).run_evidence(ungraded)

    assert isinstance(gap, Gap)
    assert gap.status is AssessStatus.UNAVAILABLE
    assert "did not grade" in gap.reason


def test_an_in_trial_pass_without_a_parity_verdict_or_g_input_is_ineligible(
    offline_store: dict[str, Any],
) -> None:
    unjudged = CoverageReader(None).run_evidence(_deferred_step(enabled=True))
    unreadable = CoverageReader(None).run_evidence(
        _deferred_step(enabled=True, parity=ParityVerdict(eligible=True))
    )

    assert isinstance(unjudged, Gap)
    assert unjudged.status is AssessStatus.INELIGIBLE
    assert unjudged.reason.startswith("coverage_parity_failed")
    assert isinstance(unreadable, Gap)
    assert unreadable.status is AssessStatus.INELIGIBLE
    assert unreadable.reason.startswith("coverage_parity_unavailable")
