"""process-compliance: stall records that cannot be read never decide a stall outcome.

A record file that is not JSON, not an object, or whose ``stalled`` is not a boolean is
skipped; a step left without a readable record is unavailable when another step has one.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus, Observation
from raidar.scoring.process_checks.stall import STALL_RECORD, stall_observations
from tests.test_process_compliance_stall import CP1, CP2, _stall, _step


def _values(*steps: StepInputs) -> dict[tuple[str, str], Observation]:
    ctx = AssessContext(problem="scb/forge", steps=tuple(steps), config={})
    return {(item.metric, item.subject): item for item in stall_observations(ctx)}


def _write(step: StepInputs, where: str, text: str) -> None:
    assert step.trace_dir is not None
    folder = step.trace_dir / where
    folder.mkdir(parents=True, exist_ok=True)
    (folder / STALL_RECORD).write_text(text, encoding="utf-8")


def test_unreadable_and_malformed_records_are_skipped_beside_a_valid_one(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    _write(step, "a", "{not json")
    _write(step, "b", json.dumps(["stalled"]))
    _write(step, "c", json.dumps({"stalled": "yes"}))
    _write(step, "d", json.dumps({"stalled": True, "continuation": {"changed": True}}))

    values = _values(step)

    assert values[("stalled", CP1)].value == 1
    assert values[("stall_resolved", CP1)].value == 1


def test_a_step_with_only_malformed_records_is_unavailable_when_another_has_one(
    tmp_path: Path,
) -> None:
    good, bad = _step(tmp_path, CP1), _step(tmp_path, CP2)
    _stall(good, ".", stalled=False)
    _write(bad, ".", "{not json")

    values = _values(good, bad)

    assert values[("stalled", CP2)].status is AssessStatus.UNAVAILABLE
    assert values[("stall_rate", TRAJECTORY_SUBJECT)].status is AssessStatus.UNAVAILABLE


def test_a_reached_step_without_a_trace_directory_has_no_record(tmp_path: Path) -> None:
    good = _step(tmp_path, CP1)
    _stall(good, ".", stalled=True, changed=True)
    untraced = replace(_step(tmp_path, CP2), trace_dir=None)

    values = _values(good, untraced)

    assert values[("stalled", CP2)].status is AssessStatus.UNAVAILABLE


def test_an_unfinished_continuation_leaves_a_stall_unresolved(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    _write(step, ".", json.dumps({"stalled": True, "continuation": None}))

    assert _values(step)[("stall_resolved", CP1)].value == 0
