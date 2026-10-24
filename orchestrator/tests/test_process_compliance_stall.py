"""process-compliance: whether a session stalled on a question.

The stall outcomes are read from ``continue-on-stall.json`` records (one per
session, filed with that session's logs): ``stalled`` and ``stall_resolved``
per step, ``stall_rate`` and ``stalled_steps`` over the trajectory. A
trajectory without any record ran without the wrapper: not applicable, never 0.
"""

from __future__ import annotations

import json
from pathlib import Path

from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.process_checks import deterministic_process_compliance_scorer
from raidar.scoring.process_checks.manifest import PROCESS_COMPLIANCE
from raidar.scoring.process_checks.stall import NO_WRAPPER

CP1, CP2, CP3 = "checkpoint_1", "checkpoint_2", "checkpoint_3"


def _step(tmp_path: Path, name: str, *, reached: bool = True) -> StepInputs:
    trace = tmp_path / name / "agent"
    if reached:
        trace.mkdir(parents=True)
        document = {"steps": [{"step_id": 1, "source": "agent", "message": "done"}]}
        (trace / "trajectory.json").write_text(json.dumps(document))
    return StepInputs(
        step=name,
        reached=reached,
        checkpoint=name,
        snapshot=None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=trace if reached else None,
        trace_digest="t" if reached else None,
        agent_result=None,
    )


def _stall(step: StepInputs, where: str, *, stalled: bool, changed: bool | None = None) -> None:
    assert step.trace_dir is not None
    folder = step.trace_dir / where / "continue-on-stall"
    folder.mkdir(parents=True, exist_ok=True)
    continuation = {"changed": changed} if stalled else None
    record = {"stalled": stalled, "continued": stalled, "continuation": continuation}
    (folder / "continue-on-stall.json").write_text(json.dumps(record))


def _assess(*steps: StepInputs) -> AssessResult:
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    return validate_result(
        PROCESS_COMPLIANCE, ctx, deterministic_process_compliance_scorer().assess(ctx)
    )


def _values(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def test_stalls_are_counted_per_step_and_over_the_trajectory(tmp_path: Path) -> None:
    first, second, third = (_step(tmp_path, name) for name in (CP1, CP2, CP3))
    _stall(first, ".", stalled=False)
    _stall(second, "guardrail-gate/stage_1_work", stalled=True, changed=True)
    _stall(second, "guardrail-gate/stage_2_fix", stalled=False)
    _stall(third, ".", stalled=True, changed=False)

    values = _values(_assess(first, second, third))

    assert [values[("stalled", step)].value for step in (CP1, CP2, CP3)] == [0, 1, 1]
    assert values[("stall_resolved", CP1)].status is AssessStatus.NOT_APPLICABLE
    assert (values[("stall_resolved", CP2)].value, values[("stall_resolved", CP3)].value) == (1, 0)
    rate = values[("stall_rate", TRAJECTORY_SUBJECT)]
    assert (rate.value, rate.numerator, rate.denominator) == (2 / 3, 2.0, 3.0)
    assert values[("stalled_steps", TRAJECTORY_SUBJECT)].value == 2


def test_without_the_wrapper_every_stall_outcome_is_not_applicable(tmp_path: Path) -> None:
    values = _values(_assess(_step(tmp_path, CP1), _step(tmp_path, CP2)))

    for key in (("stalled", CP1), ("stall_rate", TRAJECTORY_SUBJECT)):
        assert (values[key].status, values[key].reason) == (AssessStatus.NOT_APPLICABLE, NO_WRAPPER)


def test_a_reached_step_without_a_record_is_unavailable_and_so_is_the_rate(tmp_path: Path) -> None:
    first, second = _step(tmp_path, CP1), _step(tmp_path, CP2)
    unreached = _step(tmp_path, CP3, reached=False)
    _stall(first, ".", stalled=False)

    values = _values(_assess(first, second, unreached))

    assert values[("stalled", CP2)].status is AssessStatus.UNAVAILABLE
    assert values[("stalled", CP3)].status is AssessStatus.NOT_APPLICABLE
    assert values[("stall_rate", TRAJECTORY_SUBJECT)].status is AssessStatus.UNAVAILABLE
