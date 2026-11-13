"""Transitions, observations, and accepted-lineage diffs at their refusals."""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from raidar.contract import file_io
from raidar.grading.models import ParityVerdict
from raidar.grading.records import FORK_EVIDENCE_FILE, step_record_dir
from raidar.scoring.code_quality.checkpoint import CheckpointMeasure, Gap, Measured
from raidar.scoring.code_quality.diffs import accepted_changed_current_symbols, production_symbols
from raidar.scoring.code_quality.lineage_models import (
    LineageCandidate,
    RegressionBreadthInput,
    RegressionRunEvidence,
    SymbolIdentity,
)
from raidar.scoring.code_quality.parity import parity_source
from raidar.scoring.code_quality.quality import produce_production_quality
from raidar.scoring.code_quality.regression import (
    PARITY_UNAVAILABLE,
    calculate_regression_breadth,
)
from raidar.scoring.code_quality.snapshot_tool.cli import measure
from raidar.scoring.code_quality.transition import (
    PARITY_FAILED,
    in_trial_run_evidence,
    measure_transitions,
    verifier_record_dir,
)
from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import AssessStatus
from tests.code_quality_support import (
    golden_case,
    in_memory_tools,
    materialise,
    memory_step,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _measured_pair(tmp_path: Path) -> Any:
    """Two one-function checkpoints; graph measurement is not needed by transitions."""
    files = {
        "checkpoint_1": {"app.py": "def f():\n    return 1\n"},
        "checkpoint_2": {"app.py": "def f():\n    return 2\n"},
    }
    snapshots = {name: materialise(tmp_path / name, content) for name, content in files.items()}
    tools = in_memory_tools()
    measures = []
    for name, snapshot in snapshots.items():
        step = memory_step(name, snapshot)
        rows = measure(snapshot, None, file_io)
        quality = produce_production_quality(
            step.step,
            snapshot,
            ({"path": "app.py", "role": "production"},),
            rows["files"],
            rows["symbols"],
            tools,
        )
        outcome = Measured(
            snapshot,
            ("app.py",),
            production_symbols(rows["symbols"], ("app.py",)),
            quality,
            Gap(AssessStatus.UNAVAILABLE, "graph is not a transition input"),
        )
        measures.append(CheckpointMeasure(step, outcome))
    return measures


def test_an_unreadable_production_file_leaves_rework_and_regression_unavailable(
    tmp_path: Path,
) -> None:
    measures = _measured_pair(tmp_path)
    current = measures[1].outcome
    assert isinstance(current, Measured)
    # The snapshot was measured, then its production files stop being valid UTF-8.
    for path in current.production_paths:
        (current.snapshot / path).write_bytes(b"\xff\xfe\x00")

    run = RegressionRunEvidence.model_validate(
        {
            "environment": dict.fromkeys(
                (
                    "environment_id",
                    "project_sha256",
                    "lock_sha256",
                    "platform_sha256",
                    "plugin_sha256",
                    "interpreter_sha256",
                ),
                _HASH_A,
            ),
            "corpus": {"corpus_id": _HASH_A, "manifest_sha256": _HASH_A},
            "invocation": {"argv": ["pytest"], "cwd": "/app"},
            "outcomes": [],
            "ledger": [],
            "coverage": [],
        }
    )
    (transition,) = measure_transitions(measures, lambda step: (run, run))

    for gap in (transition.rework, transition.regression):
        assert isinstance(gap, Gap)
        assert gap.status is AssessStatus.INELIGIBLE
    assert cast(Gap, transition.rework).reason.startswith("canonical_provenance_unavailable")
    assert cast(Gap, transition.regression).reason.startswith(PARITY_UNAVAILABLE)


def _stored_step(tmp_path: Path, parity: ParityVerdict | None) -> StepInputs:
    """A step whose stored snapshot sits where the trial's archive keeps it."""
    snapshot = tmp_path / "trial" / "steps" / "s1" / "artifacts" / "app"
    snapshot.mkdir(parents=True, exist_ok=True)
    record = SimpleNamespace(
        snapshot=SimpleNamespace(stored_path="steps/s1/artifacts/app"),
        coverage=SimpleNamespace(enabled=True, parity=parity),
    )
    step = memory_step("s1", snapshot)
    return dataclasses.replace(step, record=cast(Any, record))


def _record_dir(tmp_path: Path) -> Path:
    directory = verifier_record_dir(_stored_step(tmp_path, None))
    assert directory is not None
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def test_the_verifier_record_dir_is_found_from_the_stored_snapshot(tmp_path: Path) -> None:
    step = _stored_step(tmp_path, None)

    directory = verifier_record_dir(step)

    assert directory is not None
    assert directory == step_record_dir(tmp_path / "trial", "s1")
    assert verifier_record_dir(memory_step("s1", None)) is None


def test_in_trial_evidence_needs_a_record_a_passing_parity_and_a_readable_input(
    tmp_path: Path,
) -> None:
    ungraded = in_trial_run_evidence(memory_step("s1", tmp_path))
    assert isinstance(ungraded, Gap)
    assert ungraded.status is AssessStatus.UNAVAILABLE

    no_verdict = in_trial_run_evidence(_stored_step(tmp_path, None))
    assert isinstance(no_verdict, Gap)
    assert no_verdict.reason == f"{PARITY_FAILED}: no parity verdict"

    failed = ParityVerdict(eligible=False, reasons=("collection_differs", "ledger_differs"))
    refused = in_trial_run_evidence(_stored_step(tmp_path, failed))
    assert isinstance(refused, Gap)
    assert refused.reason == f"{PARITY_FAILED}: collection_differs, ledger_differs"

    passing = _stored_step(tmp_path, ParityVerdict(eligible=True))
    missing = in_trial_run_evidence(passing)
    assert isinstance(missing, Gap)
    assert missing.reason == f"{PARITY_UNAVAILABLE}: {FORK_EVIDENCE_FILE} missing"

    directory = _record_dir(tmp_path)
    (directory / FORK_EVIDENCE_FILE).write_text(json.dumps({"baseline": {}}))
    malformed = in_trial_run_evidence(passing)
    assert isinstance(malformed, Gap)
    assert malformed.status is AssessStatus.INELIGIBLE
    assert malformed.reason.startswith(PARITY_UNAVAILABLE)

    case = golden_case("synthetic_shop")
    runs = case["runs"]["checkpoint_2"]
    (directory / FORK_EVIDENCE_FILE).write_text(json.dumps(runs))
    found = in_trial_run_evidence(passing)
    assert not isinstance(found, Gap)
    assert found == (
        RegressionRunEvidence.model_validate(runs["baseline"]),
        RegressionRunEvidence.model_validate(runs["produced"]),
    )


def _symbol(name: str, *, body: str = _HASH_A, path: str = "m.py") -> SymbolIdentity:
    return SymbolIdentity(
        path=path,
        qualified_name=name,
        kind="function",
        start_line=1,
        end_line=3,
        body_sha256=body,
        structure_sha256=_HASH_A,
        signature_sha256=_HASH_A,
    )


def _candidate(
    prior: SymbolIdentity,
    current: SymbolIdentity,
    *,
    accepted: bool = True,
    body_equal: bool = True,
) -> LineageCandidate:
    return LineageCandidate(
        prior=prior,
        current=current,
        body_hash_equal=body_equal,
        structure_hash_equal=True,
        signature_hash_equal=True,
        accepted=accepted,
        ambiguous_set_id=None,
        match="exact" if prior.exact_identity == current.exact_identity else "hash",
    )


def test_only_accepted_lineages_that_changed_name_their_current_symbol() -> None:
    untouched = _symbol("untouched")
    edited_before, edited_after = _symbol("edited"), _symbol("edited", body=_HASH_B)
    renamed_before, renamed_after = _symbol("old_name"), _symbol("new_name")
    rejected = _symbol("rejected", body=_HASH_B)

    changed = accepted_changed_current_symbols(
        [
            _candidate(untouched, untouched),
            _candidate(edited_before, edited_after, body_equal=False),
            _candidate(renamed_before, renamed_after),
            _candidate(_symbol("rejected"), rejected, accepted=False, body_equal=False),
        ]
    )

    assert [item.qualified_name for item in changed] == ["edited", "new_name"]


def test_parity_source_reads_the_trials_own_g_input_when_its_pass_ran(tmp_path: Path) -> None:
    verdict = ParityVerdict(eligible=True)
    step = _stored_step(tmp_path, verdict)
    assert parity_source(memory_step("s1", tmp_path), None) is None
    assert parity_source(step, None) == (verdict, None)

    directory = _record_dir(tmp_path)
    (directory / FORK_EVIDENCE_FILE).write_text("{not json")
    assert parity_source(step, None) == (verdict, None)
    (directory / FORK_EVIDENCE_FILE).write_text("[1, 2]")
    assert parity_source(step, None) == (verdict, None)
    (directory / FORK_EVIDENCE_FILE).write_text('{"baseline": {}}')
    assert parity_source(step, None) == (verdict, {"baseline": {}})


def test_a_deferred_coverage_pass_has_no_parity_source_without_a_store(tmp_path: Path) -> None:
    step = _stored_step(tmp_path, ParityVerdict(eligible=True))
    record = cast(Any, step.record)
    record.coverage.enabled = False

    assert parity_source(step, None) is None


_LOAD = "tests/test_cart.py::test_load"


def _breadth(baseline: dict[str, Any], produced: dict[str, Any]) -> Any:
    return calculate_regression_breadth(
        RegressionBreadthInput.model_validate(
            {
                "transition_id": "t",
                "baseline": baseline,
                "produced": produced,
                "changed_symbols": [],
            }
        )
    )


def test_g_is_ineligible_when_a_runs_ledger_disagrees_with_its_outcomes() -> None:
    runs = golden_case("synthetic_shop")["runs"]["checkpoint_2"]
    produced = {**runs["produced"], "ledger": runs["produced"]["ledger"][1:]}

    result = _breadth(runs["baseline"], produced)

    assert result.regression is None
    assert result.eligibility.reasons == (PARITY_UNAVAILABLE,)


def _with_event(run: dict[str, Any], node: str, phase: str) -> dict[str, Any]:
    event = {"node_id": node, "phase": phase, "event": "subprocess.Popen"}
    return {**run, "process_events": [*run["process_events"], event]}


def test_g_refuses_process_events_it_cannot_attribute() -> None:
    runs = golden_case("synthetic_shop")["runs"]["checkpoint_2"]

    def breadth(node: str, phase: str, *, covered: bool = True) -> Any:
        produced = _with_event(runs["produced"], node, phase)
        if not covered:
            produced["coverage"] = [c for c in produced["coverage"] if c["node_id"] != node]
        return _breadth(_with_event(runs["baseline"], node, phase), produced)

    # A collection-phase process cannot be tied to any test.
    assert breadth(_LOAD, "collection").regression is None
    # A failing regression that spawned a process needs coverage for its node.
    assert breadth(_LOAD, "call", covered=False).regression is None
    assert breadth(_LOAD, "call").regression == Decimal(1)
