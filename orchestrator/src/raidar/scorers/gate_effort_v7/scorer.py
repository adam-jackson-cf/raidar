"""``gate-effort@7``: gate-effort@6 with first-pass rates that can move and cascade-free counts.

``gate-effort@6`` (``raidar.scorers.gate_effort_v6``) is released and unchanged.
``smoke-3b-2`` of the guardrails experiment showed two measurement gaps in it;
@7 keeps every @6 metric, value, status, reason and evidence item and adds:

- **First-pass clean rates that are not pinned at 0.** The work session is
  never told what the gate checks, and ``FORMAT`` (``ruff format``, which the
  gate's own ``--fix`` resolves) refused every first run in both arms, so
  ``first_pass_clean_rate`` could not move. @7 reports, per step and as rates
  over the steps that decided them (the same steps as ``first_pass_clean``,
  shadow gates included; ``not_applicable`` on a step with no source change):

  - ``first_pass_clean_except_autofix``: the first run's blocking codes were
    all auto-fixable layout codes (``AUTOFIX_CODES``: ``FORMAT``, ``I001``
    import order and the ``W291``/``W292``/``W293`` whitespace codes; a fixed,
    deliberately small set whose fixes never change behaviour) or there were none;
  - ``first_pass_clean_except_tests``: every blocking code was a tests-check
    code (``TEST.*``: missing tests, failing tests, coverage below the minimum);
  - ``first_pass_clean_except_autofix_tests``: every blocking code was one of
    the two kinds above: the first run had no code-design finding.

  A run whose blocking codes are not all listed is ``unavailable``.
- **Blocking counts without the typing cascade.** One untyped ``def`` makes
  mypy report ``no-untyped-call`` at every call site; @6 reports the cascade
  beside the totals but keeps it in them, so it dominated a shadow baseline's
  count (``smoke-3b-2`` r1: 134 of 190 at the final step). The untyped ``def``
  itself is still counted (``MYPY.no-untyped-def``, once per def). @7 adds, per
  step, ``first_run_blocking_ex_cascade`` and ``step_end_blocking_ex_cascade``
  (@6's count minus its ``no-untyped-call`` findings) and
  ``new_first_run_blocking_ex_cascade`` (the first one minus the previous gated
  step's step-end one, as @6 does), with ``total_new_first_run_blocking_ex_cascade``
  and ``final_step_end_blocking_ex_cascade``: the counts the pre-registered
  cross-mode comparison reads from iteration 3b.3 on.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from raidar.scorers.gate_effort.records import StepGate
from raidar.scorers.gate_effort.scorer import (
    Measured,
    _first_pass,
    _rate,
    _read,
    _Step,
    trajectory_values,
)
from raidar.scorers.gate_effort_v2.scorer import NO_GATE, SHADOW_TOTAL_NA, _modes, _shadowed
from raidar.scorers.gate_effort_v3.scorer import NO_SOURCE_CHANGE, unchanged_steps
from raidar.scorers.gate_effort_v4.scorer import (
    FIRST_RUN_BLOCKING,
    _Grades,
    _metric,
    new_trajectory_values,
)
from raidar.scorers.gate_effort_v5.scorer import evidence
from raidar.scorers.gate_effort_v6.scorer import (
    CODES_UNLISTED,
    FIRST_RUN_UNTYPED_CALL,
    GATE_EFFORT_V6,
    STEP_END_BLOCKING,
    STEP_END_UNTYPED_CALL,
    GateEffortV6Scorer,
    _adjusted,
    _final,
    _step_values,
    _summed,
    codes_once,
    v6_trajectory_values,
    with_new_findings,
)
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.manifest import Direction, MetricLevel, MetricType, ScorerManifest
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE
_CHECKPOINT, _TRAJECTORY = MetricLevel.CHECKPOINT, MetricLevel.TRAJECTORY
_HIGHER, _LOWER = Direction.HIGHER_BETTER, Direction.LOWER_BETTER

AUTOFIX_CODES = frozenset({"FORMAT", "I001", "W291", "W292", "W293"})
"""Blocking codes the gate's ``--fix`` resolves without changing behaviour (layout, imports)."""
TESTS_PREFIX = "TEST."
FIRST_PASS_EXCEPT_AUTOFIX = "first_pass_clean_except_autofix"
FIRST_PASS_EXCEPT_TESTS = "first_pass_clean_except_tests"
FIRST_PASS_EXCEPT_BOTH = "first_pass_clean_except_autofix_tests"
FIRST_RUN_EX_CASCADE = "first_run_blocking_ex_cascade"
STEP_END_EX_CASCADE = "step_end_blocking_ex_cascade"
NEW_EX_CASCADE = "new_first_run_blocking_ex_cascade"
TOTAL_NEW_EX_CASCADE = f"total_{NEW_EX_CASCADE}"
FINAL_STEP_END_EX_CASCADE = f"final_{STEP_END_EX_CASCADE}"


def _autofix(code: str) -> bool:
    return code in AUTOFIX_CODES


def _tests(code: str) -> bool:
    return code.startswith(TESTS_PREFIX)


def _either(code: str) -> bool:
    return _autofix(code) or _tests(code)


FIRST_PASS_VARIANTS: dict[str, tuple[Callable[[str], bool], str]] = {
    FIRST_PASS_EXCEPT_AUTOFIX: (_autofix, "auto-fixable layout codes"),
    FIRST_PASS_EXCEPT_TESTS: (_tests, "tests-check codes"),
    FIRST_PASS_EXCEPT_BOTH: (_either, "auto-fixable layout or tests-check codes"),
}
"""``{metric: (ignored code, what is ignored)}``: the first run is clean but for those codes."""

V7_STEP_METRICS = (
    *(
        _metric(
            name,
            f"The first gate run refused nothing but {what} "
            f"({', '.join(sorted(AUTOFIX_CODES))} are the auto-fixable codes).",
            _CHECKPOINT,
            MetricType.BOOLEAN,
            _HIGHER,
        )
        for name, (_, what) in FIRST_PASS_VARIANTS.items()
    ),
    _metric(
        FIRST_RUN_EX_CASCADE,
        "First-run blocking findings without the MYPY.no-untyped-call cascade.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        STEP_END_EX_CASCADE,
        "Blocking findings of the step's last decided gate run without the "
        "MYPY.no-untyped-call cascade.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        NEW_EX_CASCADE,
        "First-run blocking findings minus the previous gated step's step-end count, both "
        "without the MYPY.no-untyped-call cascade.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
    ),
)
V7_TRAJECTORY_METRICS = (
    *(
        _metric(
            f"{name}_rate",
            f"Share of decided steps whose first gate run refused nothing but {what}.",
            _TRAJECTORY,
            MetricType.RATIO,
            _HIGHER,
        )
        for name, (_, what) in FIRST_PASS_VARIANTS.items()
    ),
    _metric(
        TOTAL_NEW_EX_CASCADE,
        "Blocking findings the steps' own work added before any fix, over the trajectory, "
        "without the MYPY.no-untyped-call cascade.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
    ),
    _metric(
        FINAL_STEP_END_EX_CASCADE,
        "Blocking findings at the end of the last gated step without the "
        "MYPY.no-untyped-call cascade.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
)
GATE_EFFORT_V7 = GATE_EFFORT_V6.model_copy(
    update={
        "version": "7",
        "description": (
            "Effort a guardrail-gate commit gate cost per step, the gate's tests check and "
            "the fix rounds' correctness (gate-effort@6: a failed tests check counted once, "
            "blocking counts that compare across gate modes, the mypy no-untyped-call "
            "cascade reported separately, failing tests and missed coverage apart), plus "
            "first-pass clean rates that ignore auto-fixable layout codes, the tests check, "
            "or both, and the cross-mode blocking counts without the no-untyped-call "
            "cascade. A shadow gate reports its first-run, step-end and code outcomes only. "
            "Not applicable to an arm without a gate."
        ),
        "metrics": (*GATE_EFFORT_V6.metrics, *V7_STEP_METRICS, *V7_TRAJECTORY_METRICS),
    }
)


# ---- one step ----------------------------------------------------------------------------


def first_pass_except(gate: StepGate, ignored: Callable[[str], bool]) -> Measured:
    """1 when the first run decided and every blocking code it gave is ``ignored``."""
    first = _first_pass(gate)
    if first.status is not _OK:
        return first
    codes = codes_once(gate.runs[0])
    if codes is None:
        return Measured.gap(_UNAVAILABLE, CODES_UNLISTED)
    return Measured(1.0 if all(ignored(code) for code in codes) else 0.0)


def _minus(total: Measured, part: Measured) -> Measured:
    """``total - part``, or the first one's gap."""
    for measured in (total, part):
        if measured.status is not _OK or measured.value is None:
            return Measured.gap(measured.status, measured.reason or "not measured")
    return Measured((total.value or 0.0) - (part.value or 0.0))


def v7_step_values(step: _Step, values: Mapping[str, Measured]) -> dict[str, Measured]:
    """@7's per-step additions from @6's raw values (``new_*`` filled in over the trajectory)."""
    names = [metric.id for metric in V7_STEP_METRICS]
    if not step.inputs.reached:
        return dict.fromkeys(names, Measured.gap(_NA, "checkpoint not reached"))
    if step.gate is None or step.error is not None:
        # @6 already statused every step metric (an error, or no record): reuse its reason.
        first = values[FIRST_RUN_BLOCKING]
        return dict.fromkeys(names, Measured.gap(first.status, first.reason or "no gate record"))
    found = {
        name: first_pass_except(step.gate, ignored)
        for name, (ignored, _) in FIRST_PASS_VARIANTS.items()
    }
    found[FIRST_RUN_EX_CASCADE] = _minus(values[FIRST_RUN_BLOCKING], values[FIRST_RUN_UNTYPED_CALL])
    found[STEP_END_EX_CASCADE] = _minus(values[STEP_END_BLOCKING], values[STEP_END_UNTYPED_CALL])
    found[NEW_EX_CASCADE] = Measured.gap(_UNAVAILABLE, "not computed")
    return found


def with_new_ex_cascade(per_step: Sequence[tuple[_Step, dict[str, Measured]]]) -> None:
    """``new_first_run_blocking_ex_cascade`` from the previous gated step's end (as @6 does)."""
    previous = Measured(0.0)
    for step, values in per_step:
        if not step.inputs.reached:
            values[NEW_EX_CASCADE] = Measured.gap(_NA, "checkpoint not reached")
            continue
        if not step.gated:
            previous = Measured.gap(_UNAVAILABLE, f"{step.inputs.step} has no gate record")
            continue
        first = values[FIRST_RUN_EX_CASCADE]
        if first.status is not _OK:
            values[NEW_EX_CASCADE] = first
        elif previous.status is not _OK or previous.value is None:
            reason = f"the previous gated step's end count is unknown: {previous.reason}"
            values[NEW_EX_CASCADE] = Measured.gap(_UNAVAILABLE, reason)
        else:
            values[NEW_EX_CASCADE] = Measured((first.value or 0.0) - previous.value)
        previous = values[STEP_END_EX_CASCADE]


def _no_edit(step: _Step, values: dict[str, Measured], unchanged: frozenset[str]) -> None:
    """A step with no source change is not applicable to first-pass clean (as in @3 on)."""
    if step.gate is None or step.inputs.step not in unchanged:
        return
    for name in FIRST_PASS_VARIANTS:
        if values[name].status is _OK:
            values[name] = Measured.gap(_NA, NO_SOURCE_CHANGE)


# ---- the trajectory ----------------------------------------------------------------------


def v7_trajectory_values(
    steps: Sequence[tuple[str, Mapping[str, Measured]]],
) -> dict[str, Measured]:
    measured = [values for _, values in steps]
    return {f"{name}_rate": _rate(name, measured) for name in FIRST_PASS_VARIANTS} | {
        TOTAL_NEW_EX_CASCADE: _summed(NEW_EX_CASCADE, steps),
        FINAL_STEP_END_EX_CASCADE: _final(STEP_END_EX_CASCADE, steps),
    }


def _per_step(
    steps: list[_Step], shadow: bool, unchanged: frozenset[str], grades: _Grades
) -> list[tuple[_Step, dict[str, Measured]]]:
    """@6's per-step values plus @7's, with @6's shadow and no-edit rules applied."""
    raw = [(step, _step_values(step, grades)) for step in steps]
    with_new_findings(raw)
    extended = [(step, values | v7_step_values(step, values)) for step, values in raw]
    with_new_ex_cascade(extended)
    per_step = []
    for step, values in extended:
        adjusted = _adjusted(step, values, shadow, unchanged)
        _no_edit(step, adjusted, unchanged)
        per_step.append((step, adjusted))
    return per_step


def _observations(
    steps: list[_Step], shadow: bool, unchanged: frozenset[str], grades: _Grades
) -> list[Observation]:
    per_step = _per_step(steps, shadow, unchanged, grades)
    gated = [(step.inputs.step, values) for step, values in per_step if step.gated]
    totals = (
        trajectory_values(gated)
        | new_trajectory_values([values for _, values in gated])
        | v6_trajectory_values(gated)
        | v7_trajectory_values(gated)
    )
    if shadow:
        totals = _shadowed(totals, (*SHADOW_TOTAL_NA, "fix_round_correctness_delta"))
    return [
        measured.observation(metric, step.inputs.step)
        for step, values in per_step
        for metric, measured in values.items()
    ] + [measured.observation(metric, TRAJECTORY_SUBJECT) for metric, measured in totals.items()]


class GateEffortV7Scorer(GateEffortV6Scorer):
    """gate-effort@6 with first-pass rates that can move and cascade-free blocking counts."""

    @property
    def manifest(self) -> ScorerManifest:
        return GATE_EFFORT_V7

    def assess(self, ctx: AssessContext) -> AssessResult:
        self._missing = []
        if ctx.config:
            return statused_result(
                GATE_EFFORT_V7, ctx, AssessStatus.ERROR, "gate-effort@7 takes no config"
            )
        steps = [_read(inputs) for inputs in ctx.steps]
        if not any(step.gated for step in steps):
            return statused_result(GATE_EFFORT_V7, ctx, _NA, NO_GATE)
        modes = _modes(steps)
        if len(modes) > 1:
            reason = "the trajectory mixes shadow and enforced gate records"
            return statused_result(GATE_EFFORT_V7, ctx, AssessStatus.ERROR, reason)
        grades = _Grades(self._reader())
        observations = _observations(steps, modes == {True}, unchanged_steps(ctx), grades)
        self._missing = grades.missing
        measured = any(item.status is _OK for item in observations)
        return AssessResult(
            status=_OK if measured else _INSUFFICIENT,
            reason=None if measured else "no gate run decided anything in this trajectory",
            observations=tuple(observations),
            evidence=evidence(steps),
        )


def gate_effort_v7_scorer() -> GateEffortV7Scorer:
    """Registry factory for ``gate-effort@7`` without a store (pre-fix grades unavailable)."""
    return GateEffortV7Scorer()


def gate_effort_v7_scorer_for_store(
    store_root: Path, *, replay_only: bool = False, trial: str | None = None
) -> GateEffortV7Scorer:
    """Store-bound factory: reads pre-fix grades from the Assessment store."""
    del replay_only, trial
    return GateEffortV7Scorer(store_root)


__all__ = [
    "AUTOFIX_CODES",
    "GATE_EFFORT_V7",
    "GateEffortV7Scorer",
    "first_pass_except",
    "gate_effort_v7_scorer",
    "gate_effort_v7_scorer_for_store",
]
