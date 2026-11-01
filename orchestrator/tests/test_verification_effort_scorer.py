"""verification-effort: the effort the gate/1 checks cost, read from the host-written record.

Unit cases build each step's agent log dir by hand (``workflow/workflow.json`` with its ``gate/1``
record and fix turns) and score it with the registered scorer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus
from raidar.scoring.verification_effort import (
    VERIFICATION_EFFORT,
    verification_effort_scorer,
    verification_effort_scorer_for_store,
)
from raidar.scoring.verification_effort.records import (
    GateRecordError,
    read_step_gate,
)
from raidar.scoring.verification_effort.sources import unchanged_steps
from tests.verification_effort_support import (
    CP1,
    CP2,
    CP3,
    PRE_FIX,
    assess,
    context,
    fix_turn,
    graded,
    pre_fix_entry,
    seal,
    step,
    store_pre_fix_grade,
    trace,
    value,
    values,
    with_snapshot,
    write_gate,
    write_ungated,
)

NA = AssessStatus.NOT_APPLICABLE
UNAVAILABLE = AssessStatus.UNAVAILABLE
INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE


def _refused_then_fixed(tmp_path: Path, name: str = CP1):
    item = step(tmp_path, name)
    write_gate(
        trace(item),
        [seal(1, "refused", "tests"), seal(2, "passed", seconds=3.0)],
        fixes=(fix_turn(2),),
        pre_fix=(pre_fix_entry(),),
    )
    return item


def _clean_first_time(tmp_path: Path, name: str = CP2):
    item = step(tmp_path, name)
    write_gate(trace(item), [seal(1, "passed")])
    return item


def test_a_step_with_no_trace_ran_without_a_gate_record() -> None:
    assert read_step_gate(None) is None


def test_a_trajectory_without_gate_records_is_not_applicable_never_zero(tmp_path: Path) -> None:
    bare = step(tmp_path, CP1)
    ungated = step(tmp_path, CP2)
    write_ungated(trace(ungated))

    result = assess(bare, ungated)

    assert result.status is NA
    assert {item.status for item in result.observations} == {NA}
    assert all(item.value is None for item in result.observations)


def test_a_gated_trajectory_reports_rounds_blocking_checks_and_fix_cost(tmp_path: Path) -> None:
    result = assess(_refused_then_fixed(tmp_path), _clean_first_time(tmp_path))

    assert result.status is AssessStatus.OK
    assert [value(result, m, CP1) for m in ("gate_runs", "fix_rounds", "first_pass_clean")] == [
        2,
        1,
        0,
    ]
    assert value(result, "gate_clean_at_step_end", CP1) == 1
    assert value(result, "unresolved", CP1) == 0
    assert value(result, "rounds_to_clean", CP1) == 1
    assert value(result, "first_run_blocking", CP1) == 1
    assert value(result, "blocking_checks", CP1) == 1
    assert value(result, "fix_input_tokens", CP1) == 1000
    assert value(result, "fix_output_tokens", CP1) == 100
    assert value(result, "fix_cost_usd", CP1) == 0.25
    assert value(result, "fix_wall_s", CP1) == 30.0
    assert value(result, "gate_wall_s", CP1) == 5.0
    assert [value(result, m, CP2) for m in ("fix_rounds", "first_pass_clean", "fix_cost_usd")] == [
        0,
        1,
        0,
    ]
    assert value(result, "total_fix_rounds", TRAJECTORY_SUBJECT) == 1
    assert value(result, "total_fix_cost_usd", TRAJECTORY_SUBJECT) == 0.25
    assert value(result, "first_pass_clean_rate", TRAJECTORY_SUBJECT) == 0.5
    assert value(result, "mean_rounds_to_clean", TRAJECTORY_SUBJECT) == 0.5
    assert value(result, "total_first_run_blocking", TRAJECTORY_SUBJECT) == 1


def test_unresolved_gate_error_and_unreached_steps_are_statused_not_zeroed(
    tmp_path: Path,
) -> None:
    exhausted, errored, no_seal = (step(tmp_path, f"checkpoint_{n}") for n in (1, 2, 3))
    unreached = step(tmp_path, "checkpoint_4", reached=False)
    write_gate(
        trace(exhausted),
        [seal(1, "refused", "tests"), seal(2, "refused", "lint")],
        fixes=(fix_turn(2),),
    )
    write_gate(trace(errored), [seal(1, "error")])
    write_gate(trace(no_seal), [])

    result = assess(exhausted, errored, no_seal, unreached)

    assert value(result, "unresolved", exhausted.step) == 1
    assert values(result)[("rounds_to_clean", exhausted.step)].status is NA
    assert value(result, "gate_clean_at_step_end", exhausted.step) == 0
    assert values(result)[("gate_clean_at_step_end", errored.step)].status is INSUFFICIENT
    assert value(result, "gate_error", errored.step) == 1
    assert values(result)[("gate_error", no_seal.step)].status is INSUFFICIENT
    assert values(result)[("first_pass_clean", no_seal.step)].status is INSUFFICIENT
    assert {item.status for item in result.observations if item.subject == unreached.step} == {NA}


def test_a_missing_or_broken_record_in_a_gated_trial_is_unavailable(tmp_path: Path) -> None:
    gated, missing, broken = (step(tmp_path, f"checkpoint_{n}") for n in (1, 2, 3))
    write_gate(trace(gated), [seal(1, "passed")])
    write_ungated(trace(missing))
    (trace(broken) / "workflow" / "workflow.json").parent.mkdir(parents=True)
    (trace(broken) / "workflow" / "workflow.json").write_text("[")

    result = assess(gated, missing, broken)

    assert result.status is AssessStatus.OK
    assert value(result, "first_pass_clean", gated.step) == 1
    for other in (missing, broken):
        observed = values(result)[("first_pass_clean", other.step)]
        assert (observed.status, observed.value) == (UNAVAILABLE, None)
    assert "not JSON" in (values(result)[("first_pass_clean", broken.step)].reason or "")
    assert values(result)[("total_fix_rounds", TRAJECTORY_SUBJECT)].status is UNAVAILABLE


def test_a_gate_record_that_is_not_a_gate_record_is_an_error_never_an_empty_gate(
    tmp_path: Path,
) -> None:
    item = step(tmp_path, CP1)
    path = trace(item) / "workflow" / "workflow.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"turns": [], "gate/1": {"gates": [{"checks": []}]}}))

    with pytest.raises(GateRecordError, match="no valid verdict"):
        read_step_gate(trace(item))
    assert values(assess(item))[("gate_runs", CP1)].status is UNAVAILABLE


@pytest.mark.parametrize(
    ("field", "bad"),
    [("status", "refused"), ("status", None), ("blocking", None), ("blocking", "yes")],
)
def test_a_malformed_check_is_unavailable_never_zero_failures(
    tmp_path: Path, field: str, bad: object
) -> None:
    item = step(tmp_path, CP1)
    refused = seal(1, "refused", "tests")
    refused["checks"][0][field] = bad
    if bad is None:
        del refused["checks"][0][field]
    write_gate(trace(item), [refused])

    with pytest.raises(GateRecordError):
        read_step_gate(trace(item))
    result = assess(item)
    assert values(result)[("first_run_blocking", CP1)].status is UNAVAILABLE
    assert values(result)[("blocking_checks", CP1)].value is None


def test_totals_skip_not_applicable_steps_but_keep_missing_evidence_unavailable(
    tmp_path: Path,
) -> None:
    first = with_snapshot(tmp_path, step(tmp_path, CP1), "a = 1\n")
    second = with_snapshot(tmp_path, step(tmp_path, CP2), "a = 1\n")
    for item in (first, second):
        write_gate(trace(item), [seal(1, "refused", "tests"), seal(2, "passed")])

    result = assess(first, second)  # CP2 changed no file: its first-run blocking is not applicable

    assert values(result)[("first_run_blocking", CP2)].status is NA
    assert value(result, "total_first_run_blocking", TRAJECTORY_SUBJECT) == 1

    ungated = step(tmp_path, "checkpoint_9")
    write_ungated(trace(ungated))
    mixed = assess(first, second, ungated)
    assert values(mixed)[("total_first_run_blocking", TRAJECTORY_SUBJECT)].status is UNAVAILABLE

    malformed = seal(1, "refused", "tests")
    del malformed["checks"]
    write_gate(trace(ungated), [malformed])
    broken = assess(first, second, ungated)
    assert values(broken)[("total_first_run_blocking", TRAJECTORY_SUBJECT)].status is UNAVAILABLE


def test_a_fix_turn_that_reported_no_usage_leaves_its_cost_unavailable(tmp_path: Path) -> None:
    item = step(tmp_path, CP1)
    unknown = fix_turn(2, cost_usd=None)
    write_gate(trace(item), [seal(1, "refused", "tests"), seal(2, "passed")], fixes=(unknown,))

    result = assess(item)

    assert values(result)[("fix_cost_usd", CP1)].status is UNAVAILABLE
    assert value(result, "fix_input_tokens", CP1) == 1000
    assert values(result)[("total_fix_cost_usd", TRAJECTORY_SUBJECT)].status is UNAVAILABLE


def test_the_tests_check_failing_counts_as_one_blocking_check(tmp_path: Path) -> None:
    item = step(tmp_path, CP1)
    write_gate(trace(item), [seal(1, "refused", "tests", "types"), seal(2, "passed")])

    assert value(assess(item), "first_run_blocking", CP1) == 2


def test_a_no_edit_step_is_not_first_pass_clean(tmp_path: Path) -> None:
    steps = []
    for index, source in enumerate(["a = 1\n", "a = 1\n", "a = 2\n"], start=1):
        item = with_snapshot(tmp_path, step(tmp_path, f"checkpoint_{index}"), source)
        write_gate(trace(item), [seal(1, "passed")])
        steps.append(item)

    result = assess(*steps)

    assert values(result)[("first_pass_clean", CP2)].status is NA
    assert values(result)[("first_run_blocking", CP2)].status is NA
    assert value(result, "first_pass_clean", CP3) == 1
    assert value(result, "first_pass_clean", CP1) == 1
    assert unchanged_steps(context(*steps)) == frozenset({CP2})


def test_the_fix_turns_correctness_reads_the_pre_fix_grade_and_awaits_it_otherwise(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    store_pre_fix_grade(store, CP1, passed=2, failed=2)
    first = graded(_refused_then_fixed(tmp_path), passed=4, failed=0)
    second = graded(_clean_first_time(tmp_path), passed=4, failed=0)

    result = assess(first, second, store=store)

    assert value(result, "pre_fix_pass_rate", CP1) == 0.5
    assert value(result, "correctness_delta", CP1) == 0.5
    assert values(result)[("correctness_delta", CP2)].status is NA
    assert value(result, "fix_round_correctness_delta", TRAJECTORY_SUBJECT) == 0.5

    ungraded = assess(first, second, store=tmp_path / "empty-store")
    assert values(ungraded)[("correctness_delta", CP1)].status is UNAVAILABLE
    assert PRE_FIX[:12] in (values(ungraded)[("correctness_delta", CP1)].reason or "")
    scorer = verification_effort_scorer_for_store(tmp_path / "empty-store")
    scorer.assess(AssessContext(problem="p", steps=(first, second)))
    assert scorer.awaiting_answers()  # a later rescore, after grading, completes it


def test_config_is_refused_and_the_scorer_is_registered(tmp_path: Path) -> None:
    item = _clean_first_time(tmp_path, CP1)
    ctx = AssessContext(problem="p", steps=(item,), config={"x": 1})
    assert verification_effort_scorer().assess(ctx).status is AssessStatus.ERROR
    registration = default_scorer_registry().get("verification-effort@1")
    assert registration.load().manifest == VERIFICATION_EFFORT


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("kind", "raidar-prefix-grade"),
        ("snapshot_digest", "another-tree"),
        ("step", "another-checkpoint"),
        ("identity", "another-grader"),
        ("schema_version", 2),
    ],
)
def test_a_cached_grade_with_different_provenance_is_not_fix_correctness(
    tmp_path: Path, field: str, replacement: object
) -> None:
    store = tmp_path / "store"
    store_pre_fix_grade(store, CP1, passed=2, failed=2)
    path = next(store.rglob("*.json"))
    payload = json.loads(path.read_text())
    payload[field] = replacement
    path.write_text(json.dumps(payload))
    item = graded(_refused_then_fixed(tmp_path), passed=4, failed=0)

    result = assess(item, store=store)

    assert values(result)[("pre_fix_pass_rate", CP1)].status is UNAVAILABLE
    assert values(result)[("correctness_delta", CP1)].status is UNAVAILABLE


@pytest.mark.parametrize(
    ("keys", "replacement"),
    [
        ((), []),
        (("turns",), {}),
        (("gate/1", "gates"), {}),
        (("gate/1", "pre_fix_snapshots"), "not a list"),
        (("gate/1", "pre_fix_snapshots"), [{"round": 1}]),
        (("gate/1", "gates", 0, "checks", 0, "name"), 42),
    ],
)
def test_malformed_gate_evidence_never_becomes_zero_blocking_failures(
    tmp_path: Path, keys: tuple, replacement: object
) -> None:
    item = _refused_then_fixed(tmp_path)
    path = trace(item) / "workflow/workflow.json"
    record = json.loads(path.read_text())
    if keys:
        target = record
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = replacement
    else:
        record = replacement
    path.write_text(json.dumps(record))

    result = assess(item)

    assert values(result)[("first_run_blocking", CP1)].status is UNAVAILABLE
    assert values(result)[("total_blocking_checks", TRAJECTORY_SUBJECT)].status is UNAVAILABLE
