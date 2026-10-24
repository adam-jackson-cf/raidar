"""The outcome metrics compared across trials, and the scorer refs they are read from (F10).

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

from raidar.scoring.framework.builtin_manifests import CODE_QUALITY, CORRECTNESS, RESOURCE_USAGE
from raidar.scoring.framework.manifest import Direction, ScorerManifest
from raidar.scoring.gate_effort.autofix_metrics import REFUSAL_KINDS
from raidar.scoring.gate_effort.families import FAMILIES
from raidar.scoring.gate_effort.manifest import GATE_EFFORT
from raidar.scoring.process_checks.manifest import PROCESS_COMPLIANCE

CORRECTNESS_REF = CORRECTNESS.ref
CODE_QUALITY_REF = CODE_QUALITY.ref
RESOURCE_USAGE_REF = RESOURCE_USAGE.ref
PROCESS_COMPLIANCE_REF = PROCESS_COMPLIANCE.ref
GATE_EFFORT_REF = GATE_EFFORT.ref
"""The scorer refs analysis reads; each is its manifest's ``name@version``."""


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
    # gate-effort@10: the autofix counts each step's own work added (they compare across gate
    # modes; a shadow gate's total_autofix_* re-count its never-fixed tree at every step).
    (
        "total_new_autofix_lines_changed",
        "autofix lines each step's own work added per trial (cross-mode)",
        5.0,
    ),
    (
        "total_new_autofix_removed_blocking",
        "autofix block-tier findings each step's own work added per trial (cross-mode)",
        2.0,
    ),
    *(
        (
            f"total_new_autofix_removed_blocking.{family}",
            f"autofix block-tier findings each step's own work added: {family}",
            1.0,
        )
        for family in FAMILIES
    ),
)
GATE_METRICS: tuple[MetricDef, ...] = tuple(
    MetricDef(
        f"{GATE_PREFIX}{metric}",
        f"Gate effort: {label}",
        View.GATE,
        declared_direction(GATE_EFFORT, metric),
        min_effect,
    )
    for metric, label, min_effect in _GATE_OUTCOMES
)
"""Gate-effort outcomes: compared only when the lock declares a gate-effort scorer."""
CROSS_MODE_CUMULATIVE = frozenset(
    f"{GATE_PREFIX}{metric}"
    for metric in (
        "total_first_run_blocking",
        "total_autofix_rounds",
        "total_autofix_files_changed",
        "total_autofix_lines_changed",
        "total_autofix_removed_blocking",
        *(f"total_autofix_removed_blocking.{family}" for family in FAMILIES),
    )
)
"""Gate totals a shadow gate sums over its never-fixed tree at every step (an enforced gate's
over each step's new work), so a shadow arm's value is not comparable with an enforced arm's.
The analysis keeps them but marks them; the cross-mode forms are ``total_new_*``."""


PROCESS_PREFIX = "process."
"""Process outcomes are ``process.<process-compliance trajectory metric>``."""
PROCESS_METRICS: tuple[MetricDef, ...] = (
    MetricDef(
        f"{PROCESS_PREFIX}stall_rate",
        "Process: stall rate (steps in which a session stopped to ask, changing nothing)",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE, "stall_rate"),
        0.1,
    ),
    MetricDef(
        f"{PROCESS_PREFIX}compliance_rate",
        "Process: compliance rate (decided checks passed; a test command counts only if it ran)",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE, "compliance_rate"),
        0.1,
    ),
    MetricDef(
        f"{PROCESS_PREFIX}total_test_commands_not_run",
        "Process: test commands whose runner was not found, per trial",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE, "total_test_commands_not_run"),
        1.0,
    ),
    MetricDef(
        f"{PROCESS_PREFIX}work_session_tests_run_rate",
        "Process: work sessions that ran tests after their last edit (decided steps)",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE, "work_session_tests_run_rate"),
        0.1,
    ),
    MetricDef(
        f"{PROCESS_PREFIX}work_session_tests_run_any_rate",
        "Process: work sessions that ran the tests at all (decided steps)",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE, "work_session_tests_run_any_rate"),
        0.1,
    ),
    MetricDef(
        f"{PROCESS_PREFIX}work_session_tests_after_last_code_edit_rate",
        "Process: work sessions that ran the tests after their last non-test edit (decided steps)",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE, "work_session_tests_after_last_code_edit_rate"),
        0.1,
    ),
)
"""Process outcomes: compared only when the lock declares ``process-compliance``."""

ALL_METRICS = (*OUTCOME_METRICS, *GATE_METRICS, *PROCESS_METRICS)

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
