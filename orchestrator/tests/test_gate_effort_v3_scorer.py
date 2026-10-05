"""gate-effort@3: gate-effort@2, but a step with no source change is not first-pass clean.

The commit gate commits with ``--allow-empty``, so a step whose agent changed
no source file passes the gate's first run without checking anything new
(main-02 of the guardrails experiment: b03 r2 checkpoint 2). @3 reports that
step's ``first_pass_clean`` as ``not_applicable`` and leaves it out of the rate;
everything else scores as under @2.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.gate_effort_v2 import gate_effort_v2_scorer
from raidar.scorers.gate_effort_v3 import GATE_EFFORT_V3, gate_effort_v3_scorer
from raidar.scorers.gate_effort_v3.scorer import NO_SOURCE_CHANGE, unchanged_steps
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus
from raidar.scorers.sdk.validation import validate_result
from tests.test_gate_effort_scorer import (
    PASSED,
    REFUSED,
    _step,
    _trace,
    _value,
    _values,
    _write_gate,
)
from tests.test_gate_effort_v2_scorer import _shadow

CP1, CP2, CP3 = "checkpoint_1", "checkpoint_2", "checkpoint_3"


def _snapshot(tmp_path: Path, step: StepInputs, source: str) -> StepInputs:
    root = tmp_path / step.step / "snapshot"
    root.mkdir(parents=True, exist_ok=True)
    (root / "forge.py").write_text(source)
    (root / "ruff.toml").write_text("[lint]\n")
    return dataclasses.replace(step, snapshot=root, snapshot_digest=f"d-{step.step}")


def _context(*steps: StepInputs, starter: Path | None = None) -> AssessContext:
    return AssessContext(problem="scb/forge", steps=steps, config={}, starter=starter)


def _assess(ctx: AssessContext) -> AssessResult:
    return validate_result(GATE_EFFORT_V3, ctx, gate_effort_v3_scorer().assess(ctx))


def _enforced(tmp_path: Path, sources: list[str]) -> list[StepInputs]:
    steps = []
    for index, source in enumerate(sources, start=1):
        step = _snapshot(tmp_path, _step(tmp_path, f"checkpoint_{index}"), source)
        _write_gate(_trace(step), outputs=[PASSED], termination="gate_clean", signal=True)
        steps.append(step)
    return steps


def test_a_no_edit_step_is_not_first_pass_clean(tmp_path: Path) -> None:
    steps = _enforced(tmp_path, ["a = 1\n", "a = 1\n", "a = 2\n"])
    ctx = _context(*steps)

    result = _assess(ctx)

    assert unchanged_steps(ctx) == {CP2}
    item = _values(result)[("first_pass_clean", CP2)]
    assert (item.status, item.reason) == (AssessStatus.NOT_APPLICABLE, NO_SOURCE_CHANGE)
    assert _value(result, "first_pass_clean", CP1) == 1
    assert _value(result, "first_pass_clean", CP3) == 1
    rate = _values(result)[("first_pass_clean_rate", TRAJECTORY_SUBJECT)]
    assert (rate.value, rate.numerator, rate.denominator) == (1.0, 2.0, 2.0)
    assert _value(result, "first_run_blocking", CP2) == 0
    assert _value(result, "gate_runs", CP2) == 1
    v2 = _values(validate_result(GATE_EFFORT_V3, ctx, gate_effort_v2_scorer().assess(ctx)))
    v3 = _values(result)
    assert v2.keys() == v3.keys()
    differ = {key for key in v3 if v3[key] != v2[key]}
    assert differ == {("first_pass_clean", CP2), ("first_pass_clean_rate", TRAJECTORY_SUBJECT)}


def test_the_first_step_compares_with_the_starter_only_when_there_is_one(tmp_path: Path) -> None:
    (step,) = _enforced(tmp_path, ["a = 1\n"])
    assert unchanged_steps(_context(step)) == frozenset()
    starter = tmp_path / "starter"
    starter.mkdir()
    (starter / "forge.py").write_text("a = 1\n")

    result = _assess(_context(step, starter=starter))

    item = _values(result)[("first_pass_clean", CP1)]
    assert (item.status, item.reason) == (AssessStatus.NOT_APPLICABLE, NO_SOURCE_CHANGE)


def test_an_edited_or_unsnapshotted_step_keeps_its_value(tmp_path: Path) -> None:
    first = _snapshot(tmp_path, _step(tmp_path, CP1), "a = 1\n")
    _write_gate(_trace(first), outputs=[PASSED], termination="gate_clean", signal=True)
    refused = _snapshot(tmp_path, _step(tmp_path, CP2), "a = 2\n")
    _write_gate(_trace(refused), outputs=[REFUSED, PASSED], termination="gate_clean", signal=True)
    bare = _step(tmp_path, CP3)
    _write_gate(_trace(bare), outputs=[PASSED], termination="gate_clean", signal=True)

    result = _assess(_context(first, refused, bare))

    assert _value(result, "first_pass_clean", CP2) == 0
    assert _value(result, "first_pass_clean", CP3) == 1


def test_a_shadow_no_edit_step_is_left_out_whatever_its_verdict(tmp_path: Path) -> None:
    first = _snapshot(tmp_path, _step(tmp_path, CP1), "a = 1\n")
    second = _snapshot(tmp_path, _step(tmp_path, CP2), "a = 1\n")
    _shadow(_trace(first), REFUSED, findings=[])
    _shadow(_trace(second), REFUSED, findings=[])

    result = _assess(_context(first, second))

    item = _values(result)[("first_pass_clean", CP2)]
    assert (item.status, item.reason) == (AssessStatus.NOT_APPLICABLE, NO_SOURCE_CHANGE)
    assert _values(result)[("fix_rounds", CP2)].status is AssessStatus.NOT_APPLICABLE
    rate = _values(result)[("first_pass_clean_rate", TRAJECTORY_SUBJECT)]
    assert (rate.value, rate.denominator) == (0.0, 1.0)


def test_v3_is_registered_beside_v2_with_the_same_metrics() -> None:
    registry = default_scorer_registry()
    v2, v3 = registry.get("gate-effort@2"), registry.get("gate-effort@3")

    assert v3.load().manifest == GATE_EFFORT_V3
    assert [m.id for m in v3.manifest.metrics] == [m.id for m in v2.manifest.metrics]
    assert v2.implementation_digest() != v3.implementation_digest()
