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
import json
from pathlib import Path
from typing import Any

import pytest

from raidar.grading.coverage.store import COVERAGE_KIND, SCHEMA_VERSION, coverage_path
from raidar.grading.models import ParityVerdict
from raidar.grading.records import (
    FORK_EVIDENCE_FILE,
    CoverageRef,
    coverage_identity,
    load_step_record,
    write_step_record,
)
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.code_quality import CodeQualityScorer
from raidar.scoring.code_quality.coverage_reader import OFFLINE_NOT_PRODUCED
from raidar.scoring.code_quality.observations import CASE_EXCLUDED, EXCLUDED_METRIC, NO_RUN_EVIDENCE
from raidar.scoring.framework.builtin_manifests import CODE_QUALITY
from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.rescore import assess
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation
from raidar.scoring.framework.store import AssessmentStore
from raidar.scoring.framework.trial_inputs import load_trial_context
from raidar.scoring.framework.validation import validate_result
from tests.code_quality_support import (
    FLAKY,
    FLIPPED,
    case_trial,
    flip_run,
    golden_case,
    quality_tools,
)

pytestmark = pytest.mark.slow

SHOP_CONFIG = {"entry_file": "app.py"}
T12 = "checkpoint_1->checkpoint_2"
T23 = "checkpoint_2->checkpoint_3"


def _context(trial: Path) -> AssessContext:
    ctx = load_trial_context(trial)
    ctx.config = dict(SHOP_CONFIG)
    return ctx


def _by_key(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def _flipped_case(nodes: tuple[str, ...] = (FLAKY,)) -> dict[str, Any]:
    """synthetic_shop whose checkpoint_2 coverage run flipped ``nodes`` (grading unchanged)."""
    case = copy.deepcopy(golden_case("synthetic_shop"))
    produced = case["runs"]["checkpoint_2"]["produced"]
    for node in nodes:
        produced = flip_run(produced, node, "passed" if node.endswith("test_load") else "failed")
    case["runs"]["checkpoint_2"]["produced"] = produced
    return case


def _defer(trial: Path, store: Path | None, parity: dict[str, ParityVerdict] | None = None) -> None:
    """Turn a trial graded with the in-trial pass into one that deferred it.

    With ``store``, each step's pass moves into the store as its offline run (the same
    parity verdict, ``parity`` overriding it per step, and G input), keyed as the
    producer keys it.
    """
    for record_dir in sorted((trial / "raidar-oracle" / "steps").iterdir()):
        record = load_step_record(record_dir)
        fork_file = record_dir / FORK_EVIDENCE_FILE
        fork = json.loads(fork_file.read_text()) if fork_file.is_file() else None
        if store is not None:
            verdict = (parity or {}).get(record.step, ParityVerdict(eligible=True))
            identity = coverage_identity(record, record_dir)
            path = coverage_path(
                store, record.bundle.digest, record.step, record.snapshot.digest, identity=identity
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "kind": COVERAGE_KIND,
                "identity": identity,
                "schema_version": SCHEMA_VERSION,
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


def _scorer(store: Path | None) -> CodeQualityScorer:
    return CodeQualityScorer(tools_factory=quality_tools, store_root=store)


def _run(trial: Path, store: Path | None) -> tuple[AssessResult, bool]:
    ctx = _context(trial)
    scorer = _scorer(store)
    return validate_result(CODE_QUALITY, ctx, scorer.assess(ctx)), scorer.awaiting_answers()


def test_offline_g_gives_every_observation_the_in_trial_pass_gives(tmp_path: Path) -> None:
    case = golden_case("synthetic_shop")
    in_trial, _ = _run(case_trial(tmp_path / "in-trial", case), None)
    deferred = case_trial(tmp_path / "deferred", case)
    store = tmp_path / "store"
    _defer(deferred, store)

    offline, awaiting = _run(deferred, store)

    assert not awaiting
    assert _by_key(offline) == _by_key(in_trial)
    assert _by_key(offline)[("regression", T12)].status is AssessStatus.OK
    assert offline.status is in_trial.status


def test_a_step_whose_trial_made_the_pass_is_read_from_the_trial(tmp_path: Path) -> None:
    trial = case_trial(tmp_path / "trial", golden_case("synthetic_shop"))
    reference, _ = _run(trial, None)

    with_store, awaiting = _run(trial, tmp_path / "empty-store")

    assert not awaiting
    assert _by_key(with_store) == _by_key(reference)


def test_an_unproduced_offline_run_is_unavailable_and_the_assessment_provisional(
    tmp_path: Path,
) -> None:
    trial = case_trial(tmp_path / "trial", golden_case("synthetic_shop"))
    _defer(trial, None)

    result, awaiting = _run(trial, tmp_path / "empty-store")

    assert awaiting
    gap = _by_key(result)[("regression", T12)]
    assert gap.status is AssessStatus.UNAVAILABLE
    assert (gap.reason or "").startswith(OFFLINE_NOT_PRODUCED)
    assert _by_key(result)[("verbosity", "checkpoint_1")].status is AssessStatus.OK


def test_a_failed_offline_parity_check_is_ineligible_never_zero(tmp_path: Path) -> None:
    trial = case_trial(tmp_path / "trial", golden_case("synthetic_shop"))
    store = tmp_path / "store"
    _defer(
        trial,
        store,
        {"checkpoint_2": ParityVerdict(eligible=False, reasons=("environment_mismatch",))},
    )

    result, awaiting = _run(trial, store)

    assert not awaiting
    item = _by_key(result)[("regression", T12)]
    assert (item.status, item.value) == (AssessStatus.INELIGIBLE, None)
    assert item.reason == "coverage_parity_failed: environment_mismatch"


def test_rescore_keeps_an_awaiting_assessment_provisional_then_completes_it(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    trial = case_trial(tmp_path / "trial", case)
    produced = case_trial(tmp_path / "produced", case)
    store = AssessmentStore(tmp_path / "store")
    _defer(produced, None)
    _defer(trial, None)
    registration = default_scorer_registry().get("code-quality@9")

    first = assess(store, registration, _context(trial), trial="trial")
    stored = store.get(first.key)
    assert stored is not None
    assert stored.provisional

    # The offline run arrives (written as the producer writes it), and a rescore completes it.
    for record_dir in sorted((trial / "raidar-oracle" / "steps").iterdir()):
        record = load_step_record(record_dir)
        runs = case["runs"].get(record.step)
        identity = coverage_identity(record, record_dir)
        path = coverage_path(
            store.root, record.bundle.digest, record.step, record.snapshot.digest, identity=identity
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        fork = {"transition_step": record.step, **runs} if runs is not None else None
        path.write_text(
            json.dumps(
                {
                    "kind": COVERAGE_KIND,
                    "identity": identity,
                    "schema_version": SCHEMA_VERSION,
                    "step": record.step,
                    "snapshot_digest": record.snapshot.digest,
                    "parity": {"eligible": True, "reasons": []},
                    "differences": [],
                    "fork": fork,
                }
            )
        )
    second = assess(store, registration, _context(trial), trial="trial")
    completed = store.get(second.key)
    assert second.key == first.key
    assert completed is not None
    assert not completed.provisional
    assert _by_key(completed.result)[("regression", T12)].status is AssessStatus.OK


def test_one_flipped_case_leaves_the_case_out_not_the_step(tmp_path: Path) -> None:
    clean = case_trial(tmp_path / "clean", golden_case("synthetic_shop"))
    _defer(clean, tmp_path / "clean-store", {})
    reference = _by_key(_run(clean, tmp_path / "clean-store")[0])
    trial = case_trial(tmp_path / "trial", _flipped_case())
    store = tmp_path / "store"
    _defer(trial, store, {"checkpoint_2": FLIPPED})

    observed = _by_key(_run(trial, store)[0])

    # The flaky case passed in grading, so leaving it out gives G the clean trial's value.
    assert observed[("regression", T12)] == reference[("regression", T12)]
    problem = ("problem_regression", "trajectory")
    assert observed[problem] == reference[problem]
    excluded = observed[(EXCLUDED_METRIC, T12)]
    assert (excluded.status, excluded.value) == (AssessStatus.OK, 1.0)
    assert excluded.reason == f"{CASE_EXCLUDED}: {FLAKY}"
    assert (
        observed[(EXCLUDED_METRIC, T23)] == reference_exclusions(tmp_path)[(EXCLUDED_METRIC, T23)]
    )


def reference_exclusions(tmp_path: Path) -> dict[tuple[str, str], Observation]:
    trial = case_trial(tmp_path / "ref", golden_case("synthetic_shop"))
    _defer(trial, tmp_path / "ref-store", {})
    return _by_key(_run(trial, tmp_path / "ref-store")[0])


def test_a_trial_whose_parity_held_leaves_out_no_case(tmp_path: Path) -> None:
    trial = case_trial(tmp_path / "trial", golden_case("synthetic_shop"))
    store = tmp_path / "store"
    _defer(trial, store, {})

    observed = _by_key(_run(trial, store)[0])

    assert observed[("regression", T12)].status is AssessStatus.OK
    assert observed[(EXCLUDED_METRIC, T12)].value == 0.0


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

    observed = _by_key(_run(trial, store)[0])

    item = observed[("regression", T12)]
    assert (item.status, item.reason) == (
        AssessStatus.INELIGIBLE,
        f"coverage_parity_failed: {', '.join(reasons)}",
    )
    assert observed[(EXCLUDED_METRIC, T12)].status is AssessStatus.NOT_APPLICABLE
    assert (observed[(EXCLUDED_METRIC, T12)].reason or "").startswith(NO_RUN_EVIDENCE)


def test_the_in_trial_pass_is_reconciled_the_same_way(tmp_path: Path) -> None:
    trial = case_trial(tmp_path / "trial", _flipped_case(), parity=FLIPPED)

    observed = _by_key(_run(trial, tmp_path / "empty-store")[0])

    assert observed[("regression", T12)].status is AssessStatus.OK
    assert observed[(EXCLUDED_METRIC, T12)].value == 1.0
