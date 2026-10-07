"""code-quality@3: G leaves out a few flipped cases instead of the step (smoke-3b-4 I1).

@2 reads a step's G as ``ineligible`` whenever the coverage run's outcomes differ
from the grading run's in any case. @3 leaves out at most three cases whose
outcome flipped, when nothing else about the two runs differs, and computes G
on the rest; it reports how many cases it left out per transition. Every other
parity failure stays ``ineligible`` as in @2, and a trial whose parity held
gets @2's observations.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.code_quality.lineage_models import RegressionRunEvidence
from raidar.scorers.code_quality_v2 import CODE_QUALITY_V2, CodeQualityV2Scorer
from raidar.scorers.code_quality_v3 import CODE_QUALITY_V3, CodeQualityV3Scorer
from raidar.scorers.code_quality_v3.parity import (
    CASE_EXCLUDED,
    MAX_EXCLUDED_CASES,
    flipped_cases,
    reconcile,
)
from raidar.scorers.code_quality_v3.scorer import EXCLUDED_METRIC, NO_RUN_EVIDENCE
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Observation
from raidar.scorers.sdk.validation import validate_result
from raidar.scorers.trial_inputs import load_trial_context
from raidar.verifier.coverage_store import COVERAGE_KIND, coverage_path
from raidar.verifier.models import ParityVerdict
from raidar.verifier.records import (
    FORK_EVIDENCE_FILE,
    CoverageRef,
    load_step_record,
    write_step_record,
)
from tests.code_quality_support import case_trial, golden_case, quality_tools

SHOP_CONFIG = {"entry_file": "app.py"}
T12 = "checkpoint_1->checkpoint_2"
T23 = "checkpoint_2->checkpoint_3"
FLAKY = "tests/test_cart.py::test_parse[a]"
FLIPPED = ParityVerdict(eligible=False, reasons=("ledger_mismatch", "outcome_mismatch"))


def _context(trial: Path) -> AssessContext:
    ctx = load_trial_context(trial)
    ctx.config = dict(SHOP_CONFIG)
    return ctx


def _by_key(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def _flip(run: dict[str, Any], node: str, outcome: str = "failed") -> dict[str, Any]:
    """``run`` with ``node``'s outcome (and ledger row) set to ``outcome``."""
    flipped = copy.deepcopy(run)
    for key in ("outcomes", "ledger"):
        for item in flipped[key]:
            if item["node_id"] == node:
                item["outcome"] = outcome
    return flipped


def _flipped_case(nodes: tuple[str, ...] = (FLAKY,)) -> dict[str, Any]:
    """synthetic_shop whose checkpoint_2 coverage run flipped ``nodes`` (grading unchanged)."""
    case = copy.deepcopy(golden_case("synthetic_shop"))
    produced = case["runs"]["checkpoint_2"]["produced"]
    for node in nodes:
        produced = _flip(produced, node, "passed" if node.endswith("test_load") else "failed")
    case["runs"]["checkpoint_2"]["produced"] = produced
    return case


def _defer(trial: Path, store: Path, parity: dict[str, ParityVerdict]) -> None:
    """Move each step's coverage pass into the store as its offline run, with ``parity``."""
    for record_dir in sorted((trial / "raidar-oracle" / "steps").iterdir()):
        record = load_step_record(record_dir)
        fork_file = record_dir / FORK_EVIDENCE_FILE
        fork = json.loads(fork_file.read_text()) if fork_file.is_file() else None
        verdict = parity.get(record.step, ParityVerdict(eligible=True))
        path = coverage_path(store, record.bundle.digest, record.step, record.snapshot.digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "kind": COVERAGE_KIND,
            "step": record.step,
            "snapshot_digest": record.snapshot.digest,
            "parity": verdict.model_dump(mode="json"),
            "differences": [],
            "fork": fork,
        }
        path.write_text(json.dumps(payload))
        fork_file.unlink(missing_ok=True)
        write_step_record(
            record_dir, record.model_copy(update={"coverage": CoverageRef(enabled=False)})
        )


def _run(scorer_type: type[CodeQualityV2Scorer], trial: Path, store: Path) -> AssessResult:
    ctx = _context(trial)
    scorer = scorer_type(tools_factory=quality_tools, store_root=store)
    manifest = CODE_QUALITY_V3 if scorer_type is CodeQualityV3Scorer else CODE_QUALITY_V2
    return validate_result(manifest, ctx, scorer.assess(ctx))


def _runs(case: dict[str, Any], step: str) -> tuple[RegressionRunEvidence, RegressionRunEvidence]:
    runs = case["runs"][step]
    return (
        RegressionRunEvidence.model_validate(runs["baseline"]),
        RegressionRunEvidence.model_validate(runs["produced"]),
    )


def test_one_flipped_case_leaves_the_case_out_not_the_step(tmp_path: Path) -> None:
    clean = case_trial(tmp_path / "clean", golden_case("synthetic_shop"))
    _defer(clean, tmp_path / "clean-store", {})
    reference = _by_key(_run(CodeQualityV2Scorer, clean, tmp_path / "clean-store"))
    trial = case_trial(tmp_path / "trial", _flipped_case())
    store = tmp_path / "store"
    _defer(trial, store, {"checkpoint_2": FLIPPED})

    v2 = _by_key(_run(CodeQualityV2Scorer, trial, store))
    v3 = _by_key(_run(CodeQualityV3Scorer, trial, store))

    assert v2[("regression", T12)].status is AssessStatus.INELIGIBLE
    # The flaky case passed in grading, so leaving it out gives G the clean trial's value.
    assert v3[("regression", T12)] == reference[("regression", T12)]
    problem = ("problem_regression", "trajectory")
    assert v3[problem] == reference[problem]
    excluded = v3[(EXCLUDED_METRIC, T12)]
    assert (excluded.status, excluded.value) == (AssessStatus.OK, 1.0)
    assert excluded.reason == f"{CASE_EXCLUDED}: {FLAKY}"
    assert v3[(EXCLUDED_METRIC, T23)] == reference_v3(tmp_path)[(EXCLUDED_METRIC, T23)]


def reference_v3(tmp_path: Path) -> dict[tuple[str, str], Observation]:
    trial = case_trial(tmp_path / "ref-v3", golden_case("synthetic_shop"))
    _defer(trial, tmp_path / "ref-v3-store", {})
    return _by_key(_run(CodeQualityV3Scorer, trial, tmp_path / "ref-v3-store"))


def test_a_trial_whose_parity_held_gets_v2s_observations(tmp_path: Path) -> None:
    trial = case_trial(tmp_path / "trial", golden_case("synthetic_shop"))
    store = tmp_path / "store"
    _defer(trial, store, {})

    v2 = _by_key(_run(CodeQualityV2Scorer, trial, store))
    v3 = _by_key(_run(CodeQualityV3Scorer, trial, store))

    assert {key: value for key, value in v3.items() if key[0] != EXCLUDED_METRIC} == v2
    assert v3[(EXCLUDED_METRIC, T12)].value == 0.0


@pytest.mark.parametrize(
    "reasons",
    [
        ("environment_mismatch",),
        ("coverage_outside_outcomes", "outcome_mismatch"),
        ("ledger_mismatch", "outcome_mismatch", "process_ledger_mismatch"),
    ],
)
def test_any_other_parity_failure_stays_ineligible(
    tmp_path: Path, reasons: tuple[str, ...]
) -> None:
    trial = case_trial(tmp_path / "trial", _flipped_case())
    store = tmp_path / "store"
    _defer(trial, store, {"checkpoint_2": ParityVerdict(eligible=False, reasons=reasons)})

    v3 = _by_key(_run(CodeQualityV3Scorer, trial, store))

    item = v3[("regression", T12)]
    assert (item.status, item.reason) == (
        AssessStatus.INELIGIBLE,
        f"coverage_parity_failed: {', '.join(reasons)}",
    )
    assert v3[(EXCLUDED_METRIC, T12)].status is AssessStatus.NOT_APPLICABLE
    assert (v3[(EXCLUDED_METRIC, T12)].reason or "").startswith(NO_RUN_EVIDENCE)


def test_the_in_trial_pass_is_reconciled_the_same_way(tmp_path: Path) -> None:
    trial = case_trial(tmp_path / "trial", _flipped_case(), parity=FLIPPED)

    v3 = _by_key(_run(CodeQualityV3Scorer, trial, tmp_path / "empty-store"))

    assert v3[("regression", T12)].status is AssessStatus.OK
    assert v3[(EXCLUDED_METRIC, T12)].value == 1.0


def test_flipped_cases_names_only_outcome_flips() -> None:
    case = golden_case("synthetic_shop")
    baseline, produced = _runs(case, "checkpoint_2")
    flipped = RegressionRunEvidence.model_validate(
        _flip(case["runs"]["checkpoint_2"]["produced"], FLAKY)
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
        produced = _flip(produced, node, "error")
    every = RegressionRunEvidence.model_validate(produced)
    one = RegressionRunEvidence.model_validate(
        _flip(case["runs"]["checkpoint_2"]["produced"], FLAKY)
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
        wide_produced = _flip(wide_produced, row["node_id"])
    left = RegressionRunEvidence.model_validate(wide_base)
    right = RegressionRunEvidence.model_validate(wide_produced)

    assert len(flipped_cases(left, right) or ()) == MAX_EXCLUDED_CASES + 1
    assert reconcile(FLIPPED.reasons, left, right) is None


def test_v3_is_registered_store_bound_with_v2s_metrics_plus_the_exclusion_count() -> None:
    registry = default_scorer_registry()
    v3 = registry.get("code-quality@3")
    v2 = registry.get("code-quality@2")

    assert v3.store_implementation is not None
    assert v3.manifest.metrics[: len(v2.manifest.metrics)] == v2.manifest.metrics
    assert [metric.id for metric in v3.manifest.metrics[len(v2.manifest.metrics) :]] == [
        EXCLUDED_METRIC
    ]
    assert v3.problem_config == v2.problem_config
    assert v3.implementation_digest() != v2.implementation_digest()
