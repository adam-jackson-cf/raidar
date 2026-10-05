"""``gate-effort@4``: gate-effort@3 plus the gate's tests check and the fix rounds' correctness.

``gate-effort@3`` (``raidar.scorers.gate_effort_v3``) is released and unchanged.
@4 keeps every @3 metric, rule, status and reason (shadow rule and no-edit rule
included), with one family change: a ``TEST.*`` finding (python-guardrails@3's
tests check) is counted in the ``tests`` family, not ``other``. It adds:

- **the tests check**, read from each gate run's report (``tests`` block,
  python-guardrails@3; a run without one is ``not_applicable``):
  ``first_run_tests_passed`` and ``first_run_tests_missing`` (the first run's
  tests check passed / reported ``TEST.missing``; on a step with no source
  change both are ``not_applicable``, as ``first_pass_clean`` is),
  ``tests_passed_at_step_end``, ``tests_run_at_step_end`` and
  ``coverage_at_step_end`` (the step's last decided run), and
  ``first_run_coverage`` (owned line coverage, percent, when tests ran). They
  describe the code, not the enforcement, so a shadow gate measures them too;
- **the fix rounds' correctness**: ``pre_fix_pass_rate`` is the case pass
  rate of the workspace the step's first fix session started from
  (guardrail-gate@3's pre-fix snapshot, graded offline into the Assessment
  store by ``raidar.verifier.prefix_grade``) and ``correctness_delta`` the
  step's final case pass rate (its verifier record) minus it. A step without a
  fix round, and every step of a shadow gate, is ``not_applicable``; an
  ungraded snapshot is ``unavailable`` (the Assessment is then provisional and
  a later rescore, after grading, supersedes it);
- trajectory: ``first_run_tests_pass_rate``, ``tests_missing_rate``,
  ``tests_pass_at_step_end_rate`` (decided steps), ``mean_first_run_coverage``,
  ``mean_coverage_at_step_end``, and ``fix_round_correctness_delta``: the mean
  ``correctness_delta`` over the steps that had a fix round (numerator: the
  summed deltas, denominator: the steps).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raidar.scorers.gate_effort.families import FAMILIES, TESTS, family_of
from raidar.scorers.gate_effort.manifest import BLOCKING_FINDINGS, FIRST_RUN_BLOCKING
from raidar.scorers.gate_effort.records import (
    REPORT_SCHEMA,
    GateRunView,
    StepGate,
    _inside,
)
from raidar.scorers.gate_effort.scorer import (
    Measured,
    _all,
    _evidence,
    _first_pass,
    _incomplete,
    _rate,
    _read,
    _Step,
    outcome_values,
    trajectory_values,
    usage_values,
)
from raidar.scorers.gate_effort_v2.scorer import (
    NO_GATE,
    SHADOW_REASON,
    SHADOW_STEP_NA,
    SHADOW_TOTAL_NA,
    _modes,
    _shadowed,
)
from raidar.scorers.gate_effort_v3.scorer import (
    FIRST_PASS_CLEAN,
    GATE_EFFORT_V3,
    NO_SOURCE_CHANGE,
    unchanged_steps,
)
from raidar.scorers.sdk.context import AssessContext, StepInputs, statused_result
from raidar.scorers.sdk.manifest import (
    Direction,
    EvidenceInput,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    ScorerManifest,
    Tier,
)
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)
from raidar.verifier.prefix_store import read_pre_fix_grade

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE
_CHECKPOINT, _TRAJECTORY = MetricLevel.CHECKPOINT, MetricLevel.TRAJECTORY
TEST_CODE_PREFIX = "TEST."
TESTS_CHECK = "tests"
MISSING_CODE = "TEST.missing"
PRE_FIX_KEY = "pre_fix_snapshots"
RECORD_PATH = "guardrail-gate/guardrail-gate.json"
NO_FIX_ROUND = "no fix round in this step: no pre-fix snapshot to compare"
NO_TESTS_BLOCK = "the gate run's report has no tests check (a pack without one)"


def _metric(
    metric_id: str,
    description: str,
    level: MetricLevel,
    kind: MetricType,
    direction: Direction,
    unit: str | None = None,
) -> MetricSpec:
    return MetricSpec(
        id=metric_id,
        description=description,
        level=level,
        type=kind,
        direction=direction,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
        unit=unit,
    )


_HIGHER, _LOWER, _NEUTRAL = Direction.HIGHER_BETTER, Direction.LOWER_BETTER, Direction.NEUTRAL
NEW_STEP_METRICS = (
    _metric(
        "first_run_tests_passed",
        "The first gate run's tests check passed (the agent's own tests ran, passed, covered).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    _metric(
        "first_run_tests_missing",
        "The first gate run refused the change for having no test that ran.",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _LOWER,
    ),
    _metric(
        "first_run_coverage",
        "Owned line coverage of the first gate run's test run.",
        _CHECKPOINT,
        MetricType.REAL,
        _HIGHER,
        "percent",
    ),
    _metric(
        "tests_passed_at_step_end",
        "The step's last decided gate run's tests check passed.",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    _metric(
        "tests_run_at_step_end",
        "Tests the step's last decided gate run ran.",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    _metric(
        "coverage_at_step_end",
        "Owned line coverage of the step's last decided gate run.",
        _CHECKPOINT,
        MetricType.REAL,
        _HIGHER,
        "percent",
    ),
    _metric(
        "pre_fix_pass_rate",
        "Case pass rate of the workspace the step's first fix session started from.",
        _CHECKPOINT,
        MetricType.RATIO,
        _HIGHER,
    ),
    _metric(
        "correctness_delta",
        "The step's final case pass rate minus its pre-fix pass rate.",
        _CHECKPOINT,
        MetricType.REAL,
        _HIGHER,
    ),
)
NEW_TRAJECTORY_METRICS = (
    _metric(
        "first_run_tests_pass_rate",
        "Share of decided steps whose first gate run's tests check passed.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    _metric(
        "tests_missing_rate",
        "Share of decided steps whose first gate run found no test that ran.",
        _TRAJECTORY,
        MetricType.RATIO,
        _LOWER,
    ),
    _metric(
        "tests_pass_at_step_end_rate",
        "Share of decided steps whose last gate run's tests check passed.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    _metric(
        "mean_first_run_coverage",
        "Mean owned line coverage of the first gate runs that ran tests.",
        _TRAJECTORY,
        MetricType.REAL,
        _HIGHER,
        "percent",
    ),
    _metric(
        "mean_coverage_at_step_end",
        "Mean owned line coverage at step end, over steps whose tests ran.",
        _TRAJECTORY,
        MetricType.REAL,
        _HIGHER,
        "percent",
    ),
    _metric(
        "fix_round_correctness_delta",
        "Mean over steps with a fix round of final minus pre-fix case pass rate.",
        _TRAJECTORY,
        MetricType.REAL,
        _HIGHER,
    ),
)
GATE_EFFORT_V4 = ScorerManifest(
    id="gate-effort",
    version="4",
    description=(
        "Effort a guardrail-gate commit gate cost per step (gate-effort@3), the gate's tests "
        "check (first run and step end: passed, missing tests, owned coverage) and the fix "
        "rounds' correctness (final minus pre-fix case pass rate, the pre-fix snapshot "
        "graded offline). A shadow gate reports its first-run and code outcomes only. Not "
        "applicable to an arm without a gate."
    ),
    runtimes=GATE_EFFORT_V3.runtimes,
    evidence_required=(
        EvidenceInput.TRACE,
        EvidenceInput.ORACLE_CASES,
        EvidenceInput.SNAPSHOT_CURRENT,
        EvidenceInput.SNAPSHOT_PRIOR,
    ),
    metrics=(*GATE_EFFORT_V3.metrics, *NEW_STEP_METRICS, *NEW_TRAJECTORY_METRICS),
)
SHADOW_NEW_NA = ("pre_fix_pass_rate", "correctness_delta")
NO_EDIT_NA = ("first_run_tests_passed", "first_run_tests_missing")


# ---- families: TEST.* is the tests family --------------------------------------------


def family_v4(code: str) -> str:
    return TESTS if code.startswith(TEST_CODE_PREFIX) else family_of(code)


def _family_counts(codes: Sequence[str]) -> dict[str, int]:
    counted = dict.fromkeys(FAMILIES, 0)
    for code in codes:
        counted[family_v4(code)] += 1
    return counted


def _family_values(names: Sequence[str], runs: Sequence[GateRunView]) -> dict[str, Measured]:
    """Per-family counts (v4 map), unavailable when a run's codes are not all known."""
    partial = next((run for run in runs if not run.findings.complete), None)
    if partial is not None:
        return _all(names, Measured.gap(_UNAVAILABLE, _incomplete(partial)))
    codes = [code for run in runs for code in run.findings.codes or ()]
    counts = _family_counts(codes)
    return {
        name: Measured(float(counts[family])) for name, family in zip(names, FAMILIES, strict=True)
    }


def _findings(prefix: str, runs: Sequence[GateRunView]) -> dict[str, Measured]:
    names = [f"{prefix}.{family}" for family in FAMILIES]
    unknown = next((run for run in runs if run.findings.total is None), None)
    if unknown is not None:
        return _all([prefix, *names], Measured.gap(_UNAVAILABLE, _incomplete(unknown)))
    total = Measured(float(sum(run.findings.total or 0 for run in runs)))
    return {prefix: total} | _family_values(names, runs)


def finding_values(gate: StepGate) -> dict[str, Measured]:
    """gate-effort@1's finding values with the v4 family map."""
    first_names = [FIRST_RUN_BLOCKING, *(f"{FIRST_RUN_BLOCKING}.{f}" for f in FAMILIES)]
    first = _first_pass(gate)
    if first.status is not _OK:
        values = _all(first_names, Measured.gap(first.status, first.reason or ""))
    else:
        values = _findings(FIRST_RUN_BLOCKING, gate.runs[:1])
    decided = [run for run in gate.runs if run.decided]
    if not decided:
        names = [BLOCKING_FINDINGS, *(f"{BLOCKING_FINDINGS}.{f}" for f in FAMILIES)]
        return values | _all(names, Measured.gap(_INSUFFICIENT, first.reason or "no run decided"))
    return values | _findings(BLOCKING_FINDINGS, decided)


# ---- the tests check ------------------------------------------------------------------


def _load_report(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("schema") == REPORT_SCHEMA else None


def _single_report(trace_dir: Path, run: Mapping[str, Any]) -> dict[str, Any] | None:
    """The run's one gate report (None: no report dir, or not exactly one report)."""
    folder = _inside(trace_dir, run.get("report_dir"))
    if folder is None or not folder.is_dir():
        return None
    reports = [found for path in sorted(folder.glob("*.json")) if (found := _load_report(path))]
    return reports[0] if len(reports) == 1 else None


def _test_codes(report: Mapping[str, Any]) -> list[str]:
    """The report's blocking ``TEST.*`` codes."""
    findings = [item for item in report.get("findings") or [] if isinstance(item, dict)]
    blocking = [str(item.get("code")) for item in findings if item.get("tier") == "block"]
    return [code for code in blocking if code.startswith(TEST_CODE_PREFIX)]


def _tests_block(trace_dir: Path, run: Mapping[str, Any]) -> dict[str, Any] | None:
    """The tests block of the run's single gate report (None: no report or no block)."""
    report = _single_report(trace_dir, run)
    block = report.get("tests") if report is not None else None
    if report is None or not isinstance(block, dict):
        return None
    return {**block, "blocking_codes": _test_codes(report)}


@dataclass(frozen=True)
class _RunTests:
    decided: bool
    block: dict[str, Any] | None


def _run_tests(trace_dir: Path | None, record: Mapping[str, Any]) -> list[_RunTests]:
    runs = record.get("gate_runs") or []
    return [
        _RunTests(
            decided=isinstance(run, dict) and not run.get("error"),
            block=_tests_block(trace_dir, run) if trace_dir and isinstance(run, dict) else None,
        )
        for run in runs
    ]


def _tests_of(run: _RunTests | None, what: str) -> tuple[Measured, Measured, Measured]:
    """(tests check passed, TEST.missing, coverage) of one run, or why there is none."""
    if run is None or not run.decided:
        gap = Measured.gap(_INSUFFICIENT, f"no decided gate run for the {what}")
        return gap, gap, gap
    if run.block is None:
        gap = Measured.gap(_NA, NO_TESTS_BLOCK)
        return gap, gap, gap
    codes = run.block.get("blocking_codes") or []
    coverage = run.block.get("coverage_percent")
    measured_coverage = (
        Measured(float(coverage))
        if isinstance(coverage, int | float)
        else Measured.gap(_NA, "no test ran, so no coverage was measured")
    )
    return (
        Measured(0.0 if codes else 1.0),
        Measured(1.0 if MISSING_CODE in codes else 0.0),
        measured_coverage,
    )


def tests_values(trace_dir: Path | None, record: Mapping[str, Any]) -> dict[str, Measured]:
    runs = _run_tests(trace_dir, record)
    first = runs[0] if runs else None
    last = next((run for run in reversed(runs) if run.decided), None)
    passed, missing, first_coverage = _tests_of(first, "first run")
    end_passed, _, end_coverage = _tests_of(last, "step end")
    ran = (
        Measured(float(last.block.get("tests_ran") or 0))
        if last is not None and last.block is not None
        else end_passed
    )
    return {
        "first_run_tests_passed": passed,
        "first_run_tests_missing": missing,
        "first_run_coverage": first_coverage,
        "tests_passed_at_step_end": end_passed,
        "tests_run_at_step_end": ran,
        "coverage_at_step_end": end_coverage,
    }


# ---- the fix rounds' correctness -------------------------------------------------------

GradeReader = Callable[[str, str, str], Any]


def _pre_fix_digest(record: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(digest of the first fix's starting tree, why there is none)."""
    entries = [e for e in record.get(PRE_FIX_KEY) or [] if isinstance(e, dict)]
    if not entries:
        return None, NO_FIX_ROUND
    first = min(entries, key=lambda entry: int(entry.get("round") or 0))
    digest = first.get("tree_digest")
    if not isinstance(digest, str):
        return None, f"the pre-fix snapshot was not taken: {first.get('error')}"
    return digest, None


@dataclass
class _Grades:
    reader: GradeReader | None
    missing: list[str] = field(default_factory=list)


def correctness_values(
    inputs: StepInputs, record: Mapping[str, Any], grades: _Grades
) -> dict[str, Measured]:
    names = ("pre_fix_pass_rate", "correctness_delta")
    digest, why = _pre_fix_digest(record)
    if digest is None:
        status = _NA if why == NO_FIX_ROUND else _UNAVAILABLE
        return _all(names, Measured.gap(status, why or NO_FIX_ROUND))
    if inputs.record is None:
        return _all(names, Measured.gap(_UNAVAILABLE, "the verifier did not grade this step"))
    final = inputs.record.grade.pass_rate()
    if grades.reader is None:
        reason = "no Assessment store to read the pre-fix grade from"
        return _all(names, Measured.gap(_UNAVAILABLE, reason))
    grade = grades.reader(inputs.record.bundle.digest, inputs.step, digest)
    if grade is None:
        grades.missing.append(f"{inputs.step}:{digest[:12]}")
        reason = (
            f"pre-fix snapshot {digest[:12]} is not graded yet (make experiment-assess grades it)"
        )
        return _all(names, Measured.gap(_UNAVAILABLE, reason))
    before = grade.pass_rate()
    if before is None or final is None:
        reason = "no graded case (infrastructure failure or no case) in the final or pre-fix grade"
        return _all(names, Measured.gap(_INSUFFICIENT, reason))
    return {"pre_fix_pass_rate": Measured(before), "correctness_delta": Measured(final - before)}


# ---- one step and the trajectory -------------------------------------------------------


def _record(inputs: StepInputs) -> dict[str, Any]:
    if inputs.trace_dir is None:
        return {}
    try:
        payload = json.loads((inputs.trace_dir / RECORD_PATH).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


_V4_STEP = tuple(metric.id for metric in GATE_EFFORT_V4.metrics if metric.level is _CHECKPOINT)


def _step_values(step: _Step, grades: _Grades) -> dict[str, Measured]:
    if not step.inputs.reached:
        return _all(_V4_STEP, Measured.gap(_NA, "checkpoint not reached"))
    if step.error is not None:
        return _all(_V4_STEP, Measured.gap(_UNAVAILABLE, step.error))
    if step.gate is None:
        reason = "no guardrail-gate record for this step, though other steps have one"
        return _all(_V4_STEP, Measured.gap(_UNAVAILABLE, reason))
    record = _record(step.inputs)
    return (
        outcome_values(step.gate)
        | finding_values(step.gate)
        | usage_values(step.gate)
        | tests_values(step.inputs.trace_dir, record)
        | correctness_values(step.inputs, record, grades)
    )


def _mean(
    metric: str, steps: Sequence[Mapping[str, Measured]], what: str, *, strict: bool = False
) -> Measured:
    """Mean over the steps that measured ``metric`` (``strict``: unavailable if one lacks it)."""
    gaps = [v[metric] for v in steps if v[metric].status in (_UNAVAILABLE, _INSUFFICIENT)]
    if strict and gaps:
        return Measured.gap(_UNAVAILABLE, f"{metric} is unmeasured at {len(gaps)} step(s)")
    values = [v[metric].value for v in steps if v[metric].status is _OK]
    if not values:
        return Measured.gap(_NA, f"no step measured {what}")
    total = float(sum(value for value in values if value is not None))
    return Measured(total / len(values), numerator=total, denominator=float(len(values)))


def new_trajectory_values(steps: Sequence[Mapping[str, Measured]]) -> dict[str, Measured]:
    return {
        "first_run_tests_pass_rate": _rate("first_run_tests_passed", steps),
        "tests_missing_rate": _rate("first_run_tests_missing", steps),
        "tests_pass_at_step_end_rate": _rate("tests_passed_at_step_end", steps),
        "mean_first_run_coverage": _mean("first_run_coverage", steps, "first-run coverage"),
        "mean_coverage_at_step_end": _mean("coverage_at_step_end", steps, "step-end coverage"),
        "fix_round_correctness_delta": _mean(
            "correctness_delta", steps, "a fix round's delta", strict=True
        ),
    }


def _adjusted(
    step: _Step, values: dict[str, Measured], shadow: bool, unchanged: frozenset[str]
) -> dict[str, Measured]:
    """@3's shadow and no-edit rules, extended to the new step metrics."""
    if shadow:
        values = _shadowed(values, (*SHADOW_STEP_NA, *SHADOW_NEW_NA))
    if step.gate is not None and step.inputs.step in unchanged:
        gap = Measured.gap(_NA, NO_SOURCE_CHANGE)
        values = values | {
            name: gap for name in (FIRST_PASS_CLEAN, *NO_EDIT_NA) if values[name].status is _OK
        }
    return values


def _observations(
    steps: list[_Step], shadow: bool, unchanged: frozenset[str], grades: _Grades
) -> list[Observation]:
    per_step = [
        (step, _adjusted(step, _step_values(step, grades), shadow, unchanged)) for step in steps
    ]
    gated = [(step.inputs.step, values) for step, values in per_step if step.gated]
    totals = trajectory_values(gated) | new_trajectory_values([values for _, values in gated])
    if shadow:
        totals = _shadowed(totals, (*SHADOW_TOTAL_NA, "fix_round_correctness_delta"))
    return [
        measured.observation(metric, step.inputs.step)
        for step, values in per_step
        for metric, measured in values.items()
    ] + [measured.observation(metric, TRAJECTORY_SUBJECT) for metric, measured in totals.items()]


class GateEffortV4Scorer:
    """gate-effort@3 plus the tests check and the fix rounds' correctness, per step record."""

    def __init__(self, store_root: Path | None = None) -> None:
        self._store_root = store_root
        self._missing: list[str] = []

    @property
    def manifest(self) -> ScorerManifest:
        return GATE_EFFORT_V4

    def awaiting_answers(self) -> bool:
        """True when a pre-fix snapshot was not graded yet (a later rescore completes it)."""
        return bool(self._missing)

    def _reader(self) -> GradeReader | None:
        root = self._store_root
        if root is None:
            return None
        return lambda bundle, step, digest: read_pre_fix_grade(root, bundle, step, digest)

    def assess(self, ctx: AssessContext) -> AssessResult:
        self._missing = []
        if ctx.config:
            return statused_result(
                GATE_EFFORT_V4, ctx, AssessStatus.ERROR, "gate-effort@4 takes no config"
            )
        steps = [_read(inputs) for inputs in ctx.steps]
        if not any(step.gated for step in steps):
            return statused_result(GATE_EFFORT_V4, ctx, _NA, NO_GATE)
        modes = _modes(steps)
        if len(modes) > 1:
            reason = "the trajectory mixes shadow and enforced gate records"
            return statused_result(GATE_EFFORT_V4, ctx, AssessStatus.ERROR, reason)
        grades = _Grades(self._reader())
        observations = _observations(steps, modes == {True}, unchanged_steps(ctx), grades)
        self._missing = grades.missing
        measured = any(item.status is _OK for item in observations)
        return AssessResult(
            status=_OK if measured else _INSUFFICIENT,
            reason=None if measured else "no gate run decided anything in this trajectory",
            observations=tuple(observations),
            evidence=_evidence(steps),
        )


def gate_effort_v4_scorer() -> GateEffortV4Scorer:
    """Registry factory for ``gate-effort@4`` without a store (pre-fix grades unavailable)."""
    return GateEffortV4Scorer()


def gate_effort_v4_scorer_for_store(
    store_root: Path, *, replay_only: bool = False, trial: str | None = None
) -> GateEffortV4Scorer:
    """Store-bound factory: reads pre-fix grades from the Assessment store."""
    del replay_only, trial
    return GateEffortV4Scorer(store_root)


__all__ = [
    "GATE_EFFORT_V4",
    "SHADOW_REASON",
    "GateEffortV4Scorer",
    "family_v4",
    "gate_effort_v4_scorer",
    "gate_effort_v4_scorer_for_store",
]
