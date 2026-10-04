"""``gate-effort@1``: what a ``guardrail-gate@1`` commit gate cost the agent, step by step.

Read deterministically from each step's gate record
(``raidar.scorers.gate_effort.records``); no model is asked.

Per checkpoint:

- ``gate_runs`` and ``fix_rounds``: gate runs and the fix sessions refusals started;
- ``first_pass_clean``: the first run (after the work session) passed;
- ``gate_clean_at_step_end``: the step's last run passed (the wrapper's signal);
- ``unresolved``: every fix round was used and the gate still refused;
- ``gate_error``: a run could not decide (infrastructure; no fix round follows);
- ``rounds_to_clean``: fix rounds before the gate passed, only for steps that
  ended clean (``not_applicable`` otherwise: ``unresolved`` counts those);
- ``first_run_blocking`` and ``blocking_findings`` (summed over decided runs),
  in total and per rule family (``raidar.scorers.gate_effort.families``);
- ``fix_input_tokens``, ``fix_output_tokens``, ``fix_cost_usd``, ``fix_wall_s``:
  the fix sessions only (the work session is the unwrapped agent's cost), and
  ``gate_wall_s``, the gate runs' own time.

Statuses, never a zero for a missing value (F6):

- a trajectory with no gate record in any reached step ran without the gate:
  every observation is ``not_applicable`` (an arm without the gate, never 0);
- in a gated trajectory, a reached step without a record is ``unavailable``,
  and an unreached checkpoint is ``not_applicable``;
- a step whose session failed before any gate run, or whose gate errored, has
  ``insufficient_evidence`` for what no run decided;
- family counts are ``unavailable`` when the run's report is absent and the
  gate's printed listing was capped below its blocking total.

Trajectory rates count decided steps only (numerator and denominator are
reported); totals sum the gated steps and are ``unavailable`` when any gated
step lacks the value.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from raidar.scorers.gate_effort.families import FAMILIES, family_counts
from raidar.scorers.gate_effort.manifest import (
    BLOCKING_FINDINGS,
    FIRST_RUN_BLOCKING,
    GATE_EFFORT,
)
from raidar.scorers.gate_effort.records import (
    GateRecordError,
    GateRunView,
    RunFindings,
    StepGate,
    read_step_gate,
)
from raidar.scorers.sdk.context import AssessContext, StepInputs, statused_result
from raidar.scorers.sdk.manifest import ScorerManifest
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Evidence,
    EvidenceSource,
    Observation,
    Span,
)

NO_GATE = "no guardrail-gate@1 record: this trajectory ran without a commit gate"
_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE
RATES = {
    "first_pass_clean_rate": "first_pass_clean",
    "clean_at_step_end_rate": "gate_clean_at_step_end",
    "unresolved_rate": "unresolved",
    "gate_error_rate": "gate_error",
}
_SUMMED = (
    "fix_rounds",
    FIRST_RUN_BLOCKING,
    *(f"{FIRST_RUN_BLOCKING}.{family}" for family in FAMILIES),
    BLOCKING_FINDINGS,
    *(f"{BLOCKING_FINDINGS}.{family}" for family in FAMILIES),
    "fix_input_tokens",
    "fix_output_tokens",
    "fix_cost_usd",
    "fix_wall_s",
    "gate_wall_s",
)
TOTALS = {f"total_{metric}": metric for metric in _SUMMED}
_STEP_METRICS = tuple(
    metric.id for metric in GATE_EFFORT.metrics if metric.level.value == "checkpoint"
)


@dataclass(frozen=True)
class Measured:
    """A value, or why there is none."""

    value: float | None
    status: AssessStatus = _OK
    reason: str | None = None
    numerator: float | None = None
    denominator: float | None = None

    @classmethod
    def gap(cls, status: AssessStatus, reason: str) -> Measured:
        return cls(None, status, reason)

    def observation(self, metric: str, subject: str) -> Observation:
        return Observation(
            metric=metric,
            subject=subject,
            status=self.status,
            value=self.value,
            reason=self.reason,
            numerator=self.numerator,
            denominator=self.denominator,
        )


def _flag(value: bool) -> Measured:
    return Measured(1.0 if value else 0.0)


def _all(metrics: Sequence[str], measured: Measured) -> dict[str, Measured]:
    return dict.fromkeys(metrics, measured)


# ---- one step ------------------------------------------------------------------------


def _ended_early(gate: StepGate) -> str:
    detail = f": {gate.error}" if gate.error else ""
    return f"the step ended {gate.termination_reason} before the gate decided{detail}"


def _first_pass(gate: StepGate) -> Measured:
    if not gate.runs:
        return Measured.gap(_INSUFFICIENT, f"no gate run: {_ended_early(gate)}")
    first = gate.runs[0]
    if not first.decided:
        return Measured.gap(_INSUFFICIENT, f"the first gate run could not decide: {first.error}")
    return _flag(first.passed)


def _unresolved(gate: StepGate) -> Measured:
    if gate.termination_reason == "rounds_exhausted":
        return _flag(True)
    if gate.termination_reason == "gate_clean":
        return _flag(False)
    return Measured.gap(_INSUFFICIENT, _ended_early(gate))


def _clean_at_end(gate: StepGate) -> Measured:
    if gate.clean_at_end is None:
        return Measured.gap(_INSUFFICIENT, _ended_early(gate))
    return _flag(gate.clean_at_end)


def _rounds_to_clean(gate: StepGate) -> Measured:
    if gate.clean_at_end is None:
        return Measured.gap(_INSUFFICIENT, _ended_early(gate))
    if not gate.clean_at_end:
        return Measured.gap(
            _NA, "the gate never passed in this step (censored; counted by unresolved)"
        )
    return Measured(float(len(gate.fix_sessions)))


def _gate_error(gate: StepGate) -> Measured:
    if not gate.runs:
        return Measured.gap(_INSUFFICIENT, f"no gate run: {_ended_early(gate)}")
    errored = gate.termination_reason == "gate_error" or any(not run.decided for run in gate.runs)
    return _flag(errored)


def outcome_values(gate: StepGate) -> dict[str, Measured]:
    return {
        "gate_runs": Measured(float(len(gate.runs))),
        "fix_rounds": Measured(float(len(gate.fix_sessions))),
        "first_pass_clean": _first_pass(gate),
        "gate_clean_at_step_end": _clean_at_end(gate),
        "unresolved": _unresolved(gate),
        "gate_error": _gate_error(gate),
        "rounds_to_clean": _rounds_to_clean(gate),
    }


def _incomplete(run: GateRunView) -> str:
    findings = run.findings
    if findings.total is None:
        return (
            f"gate run {run.run}: the gate's output format is not recognised and it left no report"
        )
    return (
        f"gate run {run.run}: the gate listed {findings.listed} of {findings.total} blocking "
        "findings and left no report naming them all"
    )


def _family_values(prefix: str, runs: Sequence[GateRunView]) -> dict[str, Measured]:
    """Per-family counts over ``runs``, unavailable when a run's codes are not all known."""
    names = [f"{prefix}.{family}" for family in FAMILIES]
    partial = next((run for run in runs if not run.findings.complete), None)
    if partial is not None:
        return _all(names, Measured.gap(_UNAVAILABLE, _incomplete(partial)))
    codes = [code for run in runs for code in run.findings.codes or ()]
    counts = family_counts(codes)
    return {
        name: Measured(float(counts[family])) for name, family in zip(names, FAMILIES, strict=True)
    }


def _findings(prefix: str, runs: Sequence[GateRunView]) -> dict[str, Measured]:
    """Total and per-family blocking findings over ``runs`` (all decided)."""
    unknown = next((run for run in runs if run.findings.total is None), None)
    if unknown is not None:
        names = [prefix, *(f"{prefix}.{family}" for family in FAMILIES)]
        return _all(names, Measured.gap(_UNAVAILABLE, _incomplete(unknown)))
    total = Measured(float(sum(run.findings.total or 0 for run in runs)))
    return {prefix: total} | _family_values(prefix, runs)


def finding_values(gate: StepGate) -> dict[str, Measured]:
    first_names = [FIRST_RUN_BLOCKING, *(f"{FIRST_RUN_BLOCKING}.{f}" for f in FAMILIES)]
    first = _first_pass(gate)
    if first.status is not _OK:
        values = _all(first_names, Measured.gap(first.status, first.reason or ""))
    else:
        values = _findings(FIRST_RUN_BLOCKING, gate.runs[:1])
    decided = [run for run in gate.runs if run.decided]
    if not decided:
        names = [BLOCKING_FINDINGS, *(f"{BLOCKING_FINDINGS}.{f}" for f in FAMILIES)]
        reason = first.reason or "no gate run decided"
        return values | _all(names, Measured.gap(_INSUFFICIENT, reason))
    return values | _findings(BLOCKING_FINDINGS, decided)


def _summed(values: Sequence[float | None], missing: str) -> Measured:
    if any(value is None for value in values):
        return Measured.gap(_UNAVAILABLE, missing)
    return Measured(float(sum(value or 0.0 for value in values)))


def usage_values(gate: StepGate) -> dict[str, Measured]:
    fixes = gate.fix_sessions
    unreported = "a fix session did not report {}"
    values = {
        f"fix_{metric}": _summed(
            [session.usage[metric] for session in fixes], unreported.format(metric)
        )
        for metric in ("input_tokens", "output_tokens", "cost_usd")
    }
    values["fix_wall_s"] = _summed(
        [session.duration_s for session in fixes], "the gate record has no fix-session timing"
    )
    values["gate_wall_s"] = _summed(
        [run.duration_s for run in gate.runs], "the gate record has no gate-run timing"
    )
    return values


def step_values(gate: StepGate) -> dict[str, Measured]:
    """Every checkpoint metric of one gated step, keyed by metric id."""
    return outcome_values(gate) | finding_values(gate) | usage_values(gate)


# ---- the trajectory ------------------------------------------------------------------


@dataclass(frozen=True)
class _Step:
    inputs: StepInputs
    gate: StepGate | None
    error: str | None = None

    @property
    def gated(self) -> bool:
        return self.gate is not None or self.error is not None


def _read(inputs: StepInputs) -> _Step:
    if not inputs.reached:
        return _Step(inputs, None)
    try:
        return _Step(inputs, read_step_gate(inputs.trace_dir))
    except GateRecordError as exc:
        return _Step(inputs, None, str(exc))


def _checkpoint(step: _Step) -> dict[str, Measured]:
    """One checkpoint of a gated trajectory (some reached step has a gate record)."""
    if not step.inputs.reached:
        return _all(_STEP_METRICS, Measured.gap(_NA, "checkpoint not reached"))
    if step.error is not None:
        return _all(_STEP_METRICS, Measured.gap(_UNAVAILABLE, step.error))
    if step.gate is None:
        reason = "no guardrail-gate@1 record for this step, though other steps have one"
        return _all(_STEP_METRICS, Measured.gap(_UNAVAILABLE, reason))
    return step_values(step.gate)


def _rate(metric: str, steps: Sequence[Mapping[str, Measured]]) -> Measured:
    decided = [values[metric].value for values in steps if values[metric].status is _OK]
    if not decided:
        return Measured.gap(_INSUFFICIENT, f"no gated step decided {metric}")
    numerator = float(sum(value or 0.0 for value in decided))
    return Measured(numerator / len(decided), numerator=numerator, denominator=float(len(decided)))


def _mean_rounds(steps: Sequence[Mapping[str, Measured]]) -> Measured:
    clean = [
        values["rounds_to_clean"].value or 0.0
        for values in steps
        if values["rounds_to_clean"].status is _OK
    ]
    if not clean:
        return Measured.gap(_NA, "no gated step ended with the gate passing")
    return Measured(sum(clean) / len(clean))


def _total(metric: str, steps: Sequence[tuple[str, Mapping[str, Measured]]]) -> Measured:
    gaps = [name for name, values in steps if values[metric].status is not _OK]
    if gaps:
        return Measured.gap(_UNAVAILABLE, f"{metric} is unmeasured at {', '.join(gaps)}")
    return Measured(float(sum(values[metric].value or 0.0 for _, values in steps)))


def trajectory_values(steps: Sequence[tuple[str, Mapping[str, Measured]]]) -> dict[str, Measured]:
    """Rates over decided steps and totals over the gated steps (``steps``: gated only)."""
    measured = [values for _, values in steps]
    values: dict[str, Measured] = {"gated_steps": Measured(float(len(steps)))}
    values |= {rate: _rate(metric, measured) for rate, metric in RATES.items()}
    values["mean_rounds_to_clean"] = _mean_rounds(measured)
    return values | {total: _total(metric, steps) for total, metric in TOTALS.items()}


# ---- evidence ------------------------------------------------------------------------


def _families_text(findings: RunFindings) -> str:
    if not findings.complete:
        return f"{findings.listed} of them listed"
    counts = family_counts(findings.codes or ())
    named = [f"{family} {count}" for family, count in counts.items() if count]
    return ", ".join(named) or "none"


def run_summary(run: GateRunView) -> str:
    """One line per gate run: verdict, blocking total and its families."""
    head = f"run {run.run} after {run.after}"
    if not run.decided:
        return f"{head}: gate error ({run.error})"
    if run.passed:
        return f"{head}: passed"
    total = run.findings.total
    count = "an unknown number of" if total is None else str(total)
    verdict = "timed out" if run.timed_out else "refused"
    return f"{head}: {verdict}, {count} blocking ({_families_text(run.findings)})"


def _run_evidence(step: str, trace_dir: Path, run: GateRunView) -> Evidence | None:
    path = trace_dir / run.log if run.log else None
    if path is None or not path.is_file():
        return None
    data = path.read_bytes()
    lines = data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)
    if lines == 0:
        return None
    return Evidence(
        metric=BLOCKING_FINDINGS,
        subject=step,
        source=EvidenceSource.TRACE,
        path=run.log or "",
        sha256=hashlib.sha256(data).hexdigest(),
        kind="gate-run",
        spans=(Span(line=1, end_line=lines),),
        message=run_summary(run),
    )


def _evidence(steps: Sequence[_Step]) -> tuple[Evidence, ...]:
    found: list[Evidence | None] = []
    for step in steps:
        trace_dir = step.inputs.trace_dir
        if step.gate is None or trace_dir is None:
            continue
        found.extend(_run_evidence(step.inputs.step, trace_dir, run) for run in step.gate.runs)
    return tuple(item for item in found if item is not None)


# ---- the scorer ----------------------------------------------------------------------


def _observations(
    per_step: Sequence[tuple[_Step, dict[str, Measured]]],
) -> list[Observation]:
    observations = [
        measured.observation(metric, step.inputs.step)
        for step, values in per_step
        for metric, measured in values.items()
    ]
    gated = [(step.inputs.step, values) for step, values in per_step if step.gated]
    totals = trajectory_values(gated)
    return observations + [
        measured.observation(metric, TRAJECTORY_SUBJECT) for metric, measured in totals.items()
    ]


class GateEffortScorer:
    """Deterministic scorer over each step's guardrail-gate@1 record."""

    @property
    def manifest(self) -> ScorerManifest:
        return GATE_EFFORT

    def assess(self, ctx: AssessContext) -> AssessResult:
        if ctx.config:
            return statused_result(
                GATE_EFFORT, ctx, AssessStatus.ERROR, "gate-effort@1 takes no config"
            )
        steps = [_read(inputs) for inputs in ctx.steps]
        if not any(step.gated for step in steps):
            return statused_result(GATE_EFFORT, ctx, _NA, NO_GATE)
        per_step = [(step, _checkpoint(step)) for step in steps]
        observations = _observations(per_step)
        measured = any(item.status is _OK for item in observations)
        return AssessResult(
            status=_OK if measured else _INSUFFICIENT,
            reason=None if measured else "no gate run decided anything in this trajectory",
            observations=tuple(observations),
            evidence=_evidence(steps),
        )


def gate_effort_scorer() -> GateEffortScorer:
    """Registry factory for ``gate-effort@1``."""
    return GateEffortScorer()


__all__ = ["GateEffortScorer", "gate_effort_scorer", "run_summary", "step_values"]
