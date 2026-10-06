"""code-quality@2: code-quality@1 whose G reads the offline coverage run of a deferred step.

A trial graded with ``protocol.verifier_coverage: offline`` has no in-trial
coverage pass (``record.coverage.enabled`` false, no ``g-kernel-input.json``).
@2 reads the same parity verdict and G input from the Assessment store's
offline run, so its observations equal @1's over the same trial graded with
the in-trial pass; a run not produced yet is ``unavailable`` and the
Assessment provisional; a failed offline parity check is ``ineligible``. A
step whose trial made the pass is read exactly as @1 reads it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from raidar.scorers.builtin_manifests import CODE_QUALITY
from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.code_quality import CodeQualityScorer
from raidar.scorers.code_quality_v2 import CODE_QUALITY_V2, CodeQualityV2Scorer
from raidar.scorers.code_quality_v2.scorer import OFFLINE_NOT_PRODUCED
from raidar.scorers.rescore import assess
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Observation
from raidar.scorers.sdk.validation import validate_result
from raidar.scorers.store import AssessmentStore
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


def _context(trial: Path) -> AssessContext:
    ctx = load_trial_context(trial)
    ctx.config = dict(SHOP_CONFIG)
    return ctx


def _by_key(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def _defer(trial: Path, store: Path | None, *, parity: ParityVerdict | None = None) -> None:
    """Turn a trial graded with the in-trial pass into one that deferred it.

    With ``store``, each step's pass moves into the store as its offline run
    (the same parity verdict and G input), keyed as the producer keys it.
    """
    for record_dir in sorted((trial / "raidar-oracle" / "steps").iterdir()):
        record = load_step_record(record_dir)
        fork_file = record_dir / FORK_EVIDENCE_FILE
        fork = json.loads(fork_file.read_text()) if fork_file.is_file() else None
        if store is not None:
            verdict = parity or record.coverage.parity or ParityVerdict(eligible=True)
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


def _v2(ctx: AssessContext, store: Path | None) -> tuple[AssessResult, bool]:
    scorer = CodeQualityV2Scorer(tools_factory=quality_tools, store_root=store)
    return validate_result(CODE_QUALITY_V2, ctx, scorer.assess(ctx)), scorer.awaiting_answers()


def test_offline_g_gives_every_observation_v1_gives_with_the_in_trial_pass(
    tmp_path: Path,
) -> None:
    case = golden_case("synthetic_shop")
    in_trial = _context(case_trial(tmp_path / "in-trial", case))
    v1 = validate_result(
        CODE_QUALITY, in_trial, CodeQualityScorer(tools_factory=quality_tools).assess(in_trial)
    )
    deferred = case_trial(tmp_path / "deferred", case)
    store = tmp_path / "store"
    _defer(deferred, store)

    v2, awaiting = _v2(_context(deferred), store)

    assert not awaiting
    assert _by_key(v2) == _by_key(v1)
    assert _by_key(v2)[("regression", T12)].status is AssessStatus.OK
    assert v2.status is v1.status


def test_a_step_whose_trial_made_the_pass_is_read_as_v1_reads_it(tmp_path: Path) -> None:
    ctx = _context(case_trial(tmp_path / "trial", golden_case("synthetic_shop")))
    v1 = validate_result(
        CODE_QUALITY, ctx, CodeQualityScorer(tools_factory=quality_tools).assess(ctx)
    )

    v2, awaiting = _v2(ctx, tmp_path / "empty-store")

    assert not awaiting
    assert _by_key(v2) == _by_key(v1)


def test_an_unproduced_offline_run_is_unavailable_and_the_assessment_provisional(
    tmp_path: Path,
) -> None:
    trial = case_trial(tmp_path / "trial", golden_case("synthetic_shop"))
    _defer(trial, None)

    v2, awaiting = _v2(_context(trial), tmp_path / "empty-store")

    assert awaiting
    gap = _by_key(v2)[("regression", T12)]
    assert gap.status is AssessStatus.UNAVAILABLE
    assert (gap.reason or "").startswith(OFFLINE_NOT_PRODUCED)
    assert _by_key(v2)[("verbosity", "checkpoint_1")].status is AssessStatus.OK


def test_a_failed_offline_parity_check_is_ineligible_never_zero(tmp_path: Path) -> None:
    trial = case_trial(tmp_path / "trial", golden_case("synthetic_shop"))
    store = tmp_path / "store"
    _defer(trial, store, parity=ParityVerdict(eligible=False, reasons=("environment_mismatch",)))

    v2, awaiting = _v2(_context(trial), store)

    assert not awaiting
    item = _by_key(v2)[("regression", T12)]
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
    registration = default_scorer_registry().get("code-quality@2")

    first = assess(store, registration, _context(trial), trial="trial")
    stored = store.get(first.key)
    assert stored is not None
    assert stored.provisional

    # The offline run arrives (written as the producer writes it), and a rescore completes it.
    for record_dir in sorted((trial / "raidar-oracle" / "steps").iterdir()):
        record = load_step_record(record_dir)
        runs = case["runs"].get(record.step)
        path = coverage_path(store.root, record.bundle.digest, record.step, record.snapshot.digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        fork = {"transition_step": record.step, **runs} if runs is not None else None
        path.write_text(
            json.dumps(
                {
                    "kind": COVERAGE_KIND,
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


def test_v2_is_registered_store_bound_with_v1s_metrics() -> None:
    registration = default_scorer_registry().get("code-quality@2")
    v1 = default_scorer_registry().get("code-quality@1")

    assert registration.store_implementation is not None
    assert registration.manifest.metrics == v1.manifest.metrics
    assert registration.problem_config == v1.problem_config
    assert registration.implementation_digest() != v1.implementation_digest()
