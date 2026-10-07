"""What analysis reads per trial, and the metrics it compares (F10).

A ``TrialOutcome`` is one arm x attempt x problem trial reduced to
statused values: the trial-level metrics that effects are estimated on
and the per-checkpoint series that curves are drawn from. It is built from
the job's ``TrialRecord`` (validity, arm, attempt) and the Assessment
store (scorer observations), plus the headline metric output. An
unavailable value stays unavailable with its status and reason; it is never
zero (F6), and analysis counts it as an exclusion instead.

Every direction is read from the built-in scorer manifest of the metric it is
computed from, so "improved" means better in the sense the scorer declared
and a manifest change carries through. The one exception is the composite S,
which a Metric defines as higher-better (F15).

``primary_metric`` names the experiment's primary outcome (memo §2.3,
§10.1): the lock's pre-registered ``analysis.primary_outcome`` when it sets
one, else the checkpoint-solve rate (solved / K, the memo's ``C_r``) under
``continue`` progression and the strict ``reached`` count under
``halt_on_failure``. The score component C that the headline metric weighs is
a different quantity (``correctness``: the mean per-checkpoint case pass rate,
the SCB fork's ``C_p``) and is reported as its own metric.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from raidar.scorers.builtin_manifests import CODE_QUALITY, CORRECTNESS, RESOURCE_USAGE
from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.gate_effort_manifests import (
    GATE_EFFORT,
    GATE_EFFORT_V4,
    GATE_EFFORT_V6,
    GATE_EFFORT_V7,
    GATE_EFFORT_V8,
    GATE_EFFORT_V9,
)
from raidar.scorers.gate_effort_v9.manifest import REFUSAL_KINDS
from raidar.scorers.process_manifests import (
    PROCESS_COMPLIANCE_V3,
    PROCESS_COMPLIANCE_V4,
    PROCESS_COMPLIANCE_V5,
    PROCESS_COMPLIANCE_V6,
)
from raidar.scorers.sdk.manifest import Direction, ScorerManifest

OK = "ok"
UNASSESSED = "unassessed"
TRUNCATED = "truncated"


class AnalysisModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Value(AnalysisModel):
    """A measured value (status ``ok``) or why there is none."""

    status: str = Field(min_length=1)
    value: float | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _value_matches_status(self) -> Self:
        if (self.status == OK) != (self.value is not None):
            raise ValueError("a value is present exactly when the status is ok")
        if self.status != OK and not self.reason:
            raise ValueError(f"a {self.status} value needs a reason")
        return self

    @classmethod
    def ok(cls, value: float) -> Value:
        return cls(status=OK, value=value)

    @classmethod
    def missing(cls, status: str, reason: str) -> Value:
        return cls(status=status, reason=reason)

    @property
    def measured(self) -> bool:
        return self.status == OK


class TrialOutcome(AnalysisModel):
    trial_id: str
    job_id: str
    arm: str
    attempt: int = Field(ge=0)
    problem: str
    steps: tuple[str, ...] = Field(min_length=1)
    infra_error: bool
    infra_reasons: tuple[str, ...] = ()
    metrics: dict[str, Value] = Field(default_factory=dict)
    series: dict[str, tuple[Value, ...]] = Field(default_factory=dict)

    def metric(self, metric_id: str) -> Value:
        return self.metrics.get(metric_id) or Value.missing(
            UNASSESSED, f"{metric_id} was not assessed"
        )

    def point(self, series_id: str, index: int) -> Value:
        values = self.series.get(series_id, ())
        if index < len(values):
            return values[index]
        step = self.steps[index] if index < len(self.steps) else f"#{index}"
        return Value.missing(UNASSESSED, f"{series_id}[{step}] was not assessed")


class View(StrEnum):
    """How a metric reads a trial (design memo §10.1)."""

    HEADLINE = "headline"
    CONTINUE = "continue"
    STRICT = "strict"
    QUALITY = "quality"
    GATE = "gate"
    PROCESS = "process"


class Kind(StrEnum):
    CONTINUOUS = "continuous"
    BINARY = "binary"


@dataclass(frozen=True)
class MetricDef:
    """A compared outcome. ``min_effect`` is the default δ for the sample-size projection."""

    id: str
    label: str
    view: View
    direction: Direction
    min_effect: float
    kind: Kind = Kind.CONTINUOUS


def declared_direction(manifest: ScorerManifest, metric_id: str) -> Direction:
    """The direction ``manifest`` declares for ``metric_id``; unknown metrics are refused."""
    metric = manifest.metric(metric_id)
    if metric is None:
        raise ValueError(f"{manifest.ref} declares no metric {metric_id!r}")
    return metric.direction


HEADLINE_SCORE = "headline_score"
CORRECTNESS_METRIC = "correctness"
SOLVE_RATE = "solve_rate"
REACHED = "reached"
FINAL_SOLVED = "final_solved"
# Errata-applied solve outcomes (``correctness@1`` ``*_errata`` metrics): the same rules
# over the cases the problem's pinned errata keeps. Primary only when the experiment
# pre-registers one (``analysis.primary_outcome``), and then always reported beside its
# raw counterpart (``RAW_COUNTERPART``); ``not_applicable`` for a problem without an errata.
SOLVE_RATE_ERRATA = "solve_rate_errata"
REACHED_ERRATA = "reached_errata"
FINAL_SOLVED_ERRATA = "final_solved_errata"
QUALITY_COMPONENTS = {
    "verbosity": "V",
    "erosion": "E",
    "architecture": "H",
    "rework": "R",
    "regression": "G",
}
SCORE_POLICY_DIRECTION = Direction.HIGHER_BETTER

OUTCOME_METRICS: tuple[MetricDef, ...] = (
    MetricDef(
        HEADLINE_SCORE,
        "Composite score S (headline metric, 0-100)",
        View.HEADLINE,
        SCORE_POLICY_DIRECTION,
        5.0,
    ),
    MetricDef(
        CORRECTNESS_METRIC,
        "Correctness C (mean case pass rate, headline metric input)",
        View.HEADLINE,
        declared_direction(CORRECTNESS, "strict_pass_rate"),
        0.05,
    ),
    MetricDef(
        SOLVE_RATE,
        "Checkpoint solve rate (solved / K)",
        View.CONTINUE,
        declared_direction(CORRECTNESS, "solved_checkpoints"),
        0.125,
    ),
    MetricDef(
        REACHED,
        "Checkpoints reached (strict)",
        View.STRICT,
        declared_direction(CORRECTNESS, REACHED),
        1.0,
    ),
    MetricDef(
        FINAL_SOLVED,
        "Final checkpoint solved",
        View.CONTINUE,
        declared_direction(CORRECTNESS, "solved"),
        0.25,
        Kind.BINARY,
    ),
    MetricDef(
        SOLVE_RATE_ERRATA,
        "Checkpoint solve rate under errata (errata-applied, not the raw SCB grade)",
        View.CONTINUE,
        declared_direction(CORRECTNESS, "solved_checkpoints_errata"),
        0.125,
    ),
    MetricDef(
        REACHED_ERRATA,
        "Checkpoints reached under errata (errata-applied, strict)",
        View.STRICT,
        declared_direction(CORRECTNESS, REACHED_ERRATA),
        1.0,
    ),
    MetricDef(
        FINAL_SOLVED_ERRATA,
        "Final checkpoint solved under errata (errata-applied)",
        View.CONTINUE,
        declared_direction(CORRECTNESS, "solved_errata"),
        0.25,
        Kind.BINARY,
    ),
    *(
        MetricDef(
            name,
            f"Quality {component} ({name})",
            View.QUALITY,
            declared_direction(CODE_QUALITY, f"problem_{name}"),
            0.05,
        )
        for name, component in QUALITY_COMPONENTS.items()
    ),
)

GATE_PREFIX = "gate."
"""Gate-effort outcomes are ``gate.<gate-effort trajectory metric>``."""
_GATE_OUTCOMES: tuple[tuple[str, str, float], ...] = (
    ("first_pass_clean_rate", "first-pass clean rate (decided steps)", 0.1),
    ("clean_at_step_end_rate", "clean at step end rate (decided steps)", 0.1),
    ("unresolved_rate", "unresolved rate (fix rounds exhausted)", 0.1),
    ("gate_error_rate", "gate error rate (validity: expected 0)", 0.05),
    ("mean_rounds_to_clean", "mean fix rounds to clean (clean steps)", 0.5),
    ("total_fix_rounds", "fix rounds per trial", 1.0),
    ("total_first_run_blocking", "first-run blocking findings per trial", 5.0),
    *(
        (f"total_first_run_blocking.{family}", f"first-run blocking findings: {family}", 2.0)
        for family in FAMILIES
    ),
    ("total_blocking_findings", "blocking findings over every gate run per trial", 5.0),
    *(
        (f"total_blocking_findings.{family}", f"blocking findings over every run: {family}", 2.0)
        for family in FAMILIES
    ),
    ("total_fix_input_tokens", "fix-session input tokens per trial", 10_000.0),
    ("total_fix_output_tokens", "fix-session output tokens per trial", 1_000.0),
    ("total_fix_cost_usd", "fix-session cost per trial (USD)", 0.01),
    ("total_fix_wall_s", "fix-session wall time per trial (s)", 60.0),
    ("total_gate_wall_s", "gate-run wall time per trial (s)", 5.0),
    # gate-effort@4: the gate's tests check and the fix rounds' correctness.
    ("first_run_tests_pass_rate", "first-run tests check pass rate (decided steps)", 0.1),
    ("tests_missing_rate", "first runs refused for missing tests (decided steps)", 0.1),
    ("tests_pass_at_step_end_rate", "tests check passed at step end (decided steps)", 0.1),
    ("mean_first_run_coverage", "owned line coverage of the first run (percent)", 5.0),
    ("mean_coverage_at_step_end", "owned line coverage at step end (percent)", 5.0),
    (
        "fix_round_correctness_delta",
        "fix-round correctness delta: final minus pre-fix case pass rate",
        0.05,
    ),
    # gate-effort@6: blocking counts that compare across gate modes, the typing cascade
    # apart, and failing tests and missed coverage as separate first-run outcomes.
    (
        "total_new_first_run_blocking",
        "blocking findings each step's own work added before any fix, per trial",
        5.0,
    ),
    ("final_step_end_blocking", "blocking findings at the end of the last gated step", 5.0),
    (
        "total_first_run_untyped_call",
        "first-run mypy no-untyped-call findings (call-site cascade) per trial",
        5.0,
    ),
    (
        "final_step_end_untyped_call",
        "mypy no-untyped-call findings at the end of the last gated step",
        5.0,
    ),
    ("first_run_tests_failed_rate", "first runs whose own tests failed (steps with tests)", 0.1),
    (
        "first_run_coverage_met_rate",
        "first runs meeting the coverage minimum (steps with coverage)",
        0.1,
    ),
    ("total_first_run_failed_tests", "failing tests in first gate runs per trial", 1.0),
    # gate-effort@7: first-pass clean rates that ignore auto-fixable layout codes, the tests
    # check, or both, and the cross-mode counts without the no-untyped-call cascade.
    (
        "first_pass_clean_except_autofix_rate",
        "first runs refusing only auto-fixable layout codes, or nothing (decided steps)",
        0.1,
    ),
    (
        "first_pass_clean_except_tests_rate",
        "first runs refusing only tests-check codes, or nothing (decided steps)",
        0.1,
    ),
    (
        "first_pass_clean_except_autofix_tests_rate",
        "first runs with no code-design finding (decided steps)",
        0.1,
    ),
    (
        "total_new_first_run_blocking_ex_cascade",
        "blocking findings each step's own work added, without the no-untyped-call cascade",
        5.0,
    ),
    (
        "final_step_end_blocking_ex_cascade",
        "blocking findings at the last gated step's end, without the no-untyped-call cascade",
        5.0,
    ),
    # gate-effort@8: the cross-mode counts without the cascade and with a refused suppression
    # comment counted once.
    (
        "total_new_first_run_blocking_net",
        "blocking findings each step's own work added, no cascade, a suppression counted once",
        5.0,
    ),
    (
        "final_step_end_blocking_net",
        "blocking findings at the last gated step's end, no cascade, a suppression counted once",
        5.0,
    ),
    # gate-effort@9: guardrail-gate@4's autofix step, the counts after and before it, and the
    # fix sessions' cost and wall time by the kind of refusal that started them.
    ("total_autofix_rounds", "completed autofix passes per trial", 1.0),
    ("total_autofix_errors", "autofix passes that failed per trial (validity: expected 0)", 0.5),
    ("total_autofix_files_changed", "files the autofix passes changed per trial", 1.0),
    ("total_autofix_lines_changed", "lines the autofix passes changed per trial", 5.0),
    ("total_autofix_removed_blocking", "block-tier findings autofix fixed per trial", 2.0),
    *(
        (
            f"total_autofix_removed_blocking.{family}",
            f"block-tier findings autofix fixed: {family}",
            1.0,
        )
        for family in FAMILIES
    ),
    (
        "first_pass_clean_post_autofix_rate",
        "first checks after autofix that passed (decided steps)",
        0.1,
    ),
    (
        "total_new_first_run_blocking_post_autofix_net",
        "blocking findings each step's own work added, after autofix (net)",
        5.0,
    ),
    (
        "final_step_end_blocking_post_autofix_net",
        "blocking findings at the last gated step's end, after autofix (net)",
        5.0,
    ),
    (
        "total_new_first_run_blocking_raw_net",
        "blocking findings each step's own work added, before autofix (net)",
        5.0,
    ),
    *(
        (f"total_fix_cost_usd_by_refusal.{kind}", f"fix-session cost: {kind} refusals", 0.01)
        for kind in REFUSAL_KINDS
    ),
    *(
        (f"total_fix_wall_s_by_refusal.{kind}", f"fix-session wall time: {kind} refusals", 60.0)
        for kind in REFUSAL_KINDS
    ),
    (
        "total_fix_sessions_with_coverage_minimum",
        "fix sessions whose refusal included the coverage minimum per trial",
        1.0,
    ),
    (
        "total_fix_cost_usd_with_coverage_minimum",
        "cost of the fix sessions whose refusal included the coverage minimum",
        0.01,
    ),
    (
        "total_fix_wall_s_with_coverage_minimum",
        "wall time of the fix sessions whose refusal included the coverage minimum",
        60.0,
    ),
)
GATE_METRICS: tuple[MetricDef, ...] = tuple(
    MetricDef(
        f"{GATE_PREFIX}{metric}",
        f"Gate effort: {label}",
        View.GATE,
        declared_direction(GATE_EFFORT_V9, metric),
        min_effect,
    )
    for metric, label, min_effect in _GATE_OUTCOMES
)
"""Gate-effort outcomes: compared only when the lock declares a gate-effort scorer."""
_GATE_V4_ONLY = frozenset(
    f"{GATE_PREFIX}{metric.id}"
    for metric in GATE_EFFORT_V4.metrics
    if metric.id not in {m.id for m in GATE_EFFORT.metrics}
)


_GATE_V6_ONLY = frozenset(
    f"{GATE_PREFIX}{metric.id}"
    for metric in GATE_EFFORT_V6.metrics
    if metric.id not in {m.id for m in GATE_EFFORT_V4.metrics}
)


_GATE_V7_ONLY = frozenset(
    f"{GATE_PREFIX}{metric.id}"
    for metric in GATE_EFFORT_V7.metrics
    if metric.id not in {m.id for m in GATE_EFFORT_V6.metrics}
)


_GATE_V8_ONLY = frozenset(
    f"{GATE_PREFIX}{metric.id}"
    for metric in GATE_EFFORT_V8.metrics
    if metric.id not in {m.id for m in GATE_EFFORT_V7.metrics}
)


_GATE_V9_ONLY = frozenset(
    f"{GATE_PREFIX}{metric.id}"
    for metric in GATE_EFFORT_V9.metrics
    if metric.id not in {m.id for m in GATE_EFFORT_V8.metrics}
)


def gate_metrics_for(ref: str | None) -> tuple[MetricDef, ...]:
    """The gate outcomes a gate-effort version reports.

    ``@4`` and ``@5`` add the tests check; ``@6`` adds the mode-comparable blocking counts,
    the typing cascade and the split first-run tests outcomes; ``@7`` the first-pass rates
    that ignore auto-fixable codes or the tests check, and the cascade-free counts; ``@8``
    the counts that also count a refused suppression comment once (``*_net``); ``@9`` the
    autofix step, the counts after and before it, and the fix cost by refusal kind.
    """
    if ref == "gate-effort@9":
        return GATE_METRICS
    if ref == "gate-effort@8":
        left_out = _GATE_V9_ONLY
    elif ref == "gate-effort@7":
        left_out = _GATE_V8_ONLY | _GATE_V9_ONLY
    elif ref == "gate-effort@6":
        left_out = _GATE_V7_ONLY | _GATE_V8_ONLY | _GATE_V9_ONLY
    elif ref in {"gate-effort@4", "gate-effort@5"}:
        left_out = _GATE_V6_ONLY | _GATE_V7_ONLY | _GATE_V8_ONLY | _GATE_V9_ONLY
    else:
        left_out = _GATE_V4_ONLY | _GATE_V6_ONLY | _GATE_V7_ONLY | _GATE_V8_ONLY | _GATE_V9_ONLY
    return tuple(metric for metric in GATE_METRICS if metric.id not in left_out)


PROCESS_PREFIX = "process."
"""Process outcomes are ``process.<process-compliance@3 to @6 trajectory metric>``."""
PROCESS_METRICS: tuple[MetricDef, ...] = (
    MetricDef(
        f"{PROCESS_PREFIX}stall_rate",
        "Process: stall rate (steps in which a session stopped to ask, changing nothing)",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE_V3, "stall_rate"),
        0.1,
    ),
)
"""Process outcomes (``process-compliance@3``): compared only when the lock declares it."""
PROCESS_V4_METRICS: tuple[MetricDef, ...] = (
    *PROCESS_METRICS,
    MetricDef(
        f"{PROCESS_PREFIX}compliance_rate",
        "Process: compliance rate (decided checks passed; a test command counts only if it ran)",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE_V4, "compliance_rate"),
        0.1,
    ),
    MetricDef(
        f"{PROCESS_PREFIX}total_test_commands_not_run",
        "Process: test commands whose runner was not found, per trial",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE_V4, "total_test_commands_not_run"),
        1.0,
    ),
)
"""Process outcomes of ``process-compliance@4``: @3's plus the compliance rate and not-run tests."""
PROCESS_V5_METRICS: tuple[MetricDef, ...] = (
    *PROCESS_V4_METRICS,
    MetricDef(
        f"{PROCESS_PREFIX}work_session_tests_run_rate",
        "Process: work sessions that ran tests after their last edit (decided steps)",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE_V5, "work_session_tests_run_rate"),
        0.1,
    ),
)
"""Process outcomes of ``process-compliance@5``: @4's plus the work session's own test runs
(its ``compliance_rate`` counts the plan check only when the condition asks for a plan)."""
PROCESS_V6_METRICS: tuple[MetricDef, ...] = (
    *PROCESS_V5_METRICS,
    MetricDef(
        f"{PROCESS_PREFIX}work_session_tests_run_any_rate",
        "Process: work sessions that ran the tests at all (decided steps)",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE_V6, "work_session_tests_run_any_rate"),
        0.1,
    ),
    MetricDef(
        f"{PROCESS_PREFIX}work_session_tests_after_last_code_edit_rate",
        "Process: work sessions that ran the tests after their last non-test edit (decided steps)",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE_V6, "work_session_tests_after_last_code_edit_rate"),
        0.1,
    ),
)
"""Process outcomes of ``process-compliance@6``: @5's plus whether the work session ran the
tests at all, and after its last edit to a non-test file."""
PROCESS_OUTCOME_SCORER = "process-compliance@3"
PROCESS_OUTCOME_SCORERS = (
    "process-compliance@6",
    "process-compliance@5",
    "process-compliance@4",
    PROCESS_OUTCOME_SCORER,
)
"""The process-compliance versions with ``process.*`` outcomes, newest first."""


def process_metrics_for(ref: str | None) -> tuple[MetricDef, ...]:
    """The process outcomes a process-compliance version reports (none before ``@3``)."""
    if ref == "process-compliance@6":
        return PROCESS_V6_METRICS
    if ref == "process-compliance@5":
        return PROCESS_V5_METRICS
    if ref == "process-compliance@4":
        return PROCESS_V4_METRICS
    return PROCESS_METRICS if ref == PROCESS_OUTCOME_SCORER else ()


ALL_METRICS = (*OUTCOME_METRICS, *GATE_METRICS, *PROCESS_V6_METRICS)

VERIFIER_PREFIX = "verifier."
"""Verifier diagnostics: described per arm, never compared (no verdict, no effect)."""
VERIFIER_DISAGREEMENTS = f"{VERIFIER_PREFIX}grading_coverage_disagreements"
VERIFIER_DISAGREEMENT_SOURCE = "code-quality@3"
VERIFIER_METRICS: tuple[MetricDef, ...] = (
    MetricDef(
        VERIFIER_DISAGREEMENTS,
        "Verifier: grader cases whose outcome differed between the grading run and the "
        "coverage run, summed over a trial's transitions (code-quality@3 "
        "regression_parity_excluded_cases; diagnostic, never an outcome)",
        View.QUALITY,
        Direction.NEUTRAL,
        1.0,
    ),
)

TOTAL_COST = "total_cost_usd"
COST_METRICS = ("total_cost_usd", "total_tokens", "total_turns", "total_agent_wall_s")


RAW_COUNTERPART = {
    SOLVE_RATE_ERRATA: SOLVE_RATE,
    REACHED_ERRATA: REACHED,
    FINAL_SOLVED_ERRATA: FINAL_SOLVED,
}
"""The raw SCB outcome an errata-applied primary is always reported beside."""
PRIMARY_FROM_LOCK = "lock (analysis.primary_outcome)"
PRIMARY_DEFAULT = "default for {progression} progression"


def primary_metric(progression: str, configured: str | None = None) -> str:
    """The primary outcome: the pre-registered one, else the progression's (memo §2.3, §10.1)."""
    if configured is not None:
        return outcome_metric(configured).id
    if progression == "continue":
        return SOLVE_RATE
    if progression == "halt_on_failure":
        return REACHED
    raise ValueError(f"no primary outcome is defined for progression {progression!r}")


def primary_source(progression: str, configured: str | None = None) -> str:
    """Where the primary outcome comes from, for the report's label."""
    return PRIMARY_FROM_LOCK if configured else PRIMARY_DEFAULT.format(progression=progression)


@dataclass(frozen=True)
class SeriesDef:
    id: str
    label: str
    view: View
    direction: Direction


SOLVED = "solved"
STRICT_SOLVED = "strict_solved"


def _series(series_id: str, label: str, view: View, manifest: ScorerManifest) -> SeriesDef:
    return SeriesDef(series_id, label, view, declared_direction(manifest, series_id))


CHECKPOINT_SERIES: tuple[SeriesDef, ...] = (
    _series(SOLVED, "Solved (continue view)", View.CONTINUE, CORRECTNESS),
    SeriesDef(
        STRICT_SOLVED,
        "Solved within the reached prefix (strict view)",
        View.STRICT,
        declared_direction(CORRECTNESS, SOLVED),
    ),
    _series("strict_pass_rate", "Case pass rate", View.CONTINUE, CORRECTNESS),
    _series("verbosity", "Verbosity V", View.QUALITY, CODE_QUALITY),
    _series("erosion", "Erosion E", View.QUALITY, CODE_QUALITY),
    _series("architecture", "Architecture H", View.QUALITY, CODE_QUALITY),
    _series("cost_usd", "Cost per checkpoint (USD)", View.CONTINUE, RESOURCE_USAGE),
)


def outcome_metric(metric_id: str) -> MetricDef:
    try:
        return next(metric for metric in ALL_METRICS if metric.id == metric_id)
    except StopIteration as exc:
        known = ", ".join(metric.id for metric in ALL_METRICS)
        raise ValueError(f"unknown outcome metric {metric_id!r}; known: {known}") from exc
