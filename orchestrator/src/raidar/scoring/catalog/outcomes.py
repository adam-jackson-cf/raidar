"""The outcome metrics compared across trials, and the scorer refs they are read from (F10).

Every direction is read from the built-in scorer manifest of the metric it is
computed from, so "improved" means better in the sense the scorer declared
and a manifest change carries through. The one exception is the composite S,
which a Metric defines as higher-better (F15).

``primary_metric`` names the experiment's primary outcome (memo §2.3,
§10.1): the lock's pre-registered ``analysis.primary_outcome`` when it sets
one, else the solvable-set solve rate (checkpoints whose every solvable case passes, / K).
The raw SCB outcome is always reported beside it (``RAW_COUNTERPART``).
The score component C that the headline metric weighs is
a different quantity (``correctness``: the mean per-checkpoint case pass rate,
the SCB fork's ``C_p``) and is reported as its own metric.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from raidar.scoring.framework.builtin_manifests import CODE_QUALITY, CORRECTNESS, RESOURCE_USAGE
from raidar.scoring.framework.manifest import Direction, ScorerManifest
from raidar.scoring.process_checks.manifest import PROCESS_COMPLIANCE
from raidar.scoring.verification_effort.manifest import VERIFICATION_EFFORT

CORRECTNESS_REF = CORRECTNESS.ref
CODE_QUALITY_REF = CODE_QUALITY.ref
RESOURCE_USAGE_REF = RESOURCE_USAGE.ref
PROCESS_COMPLIANCE_REF = PROCESS_COMPLIANCE.ref
VERIFICATION_EFFORT_REF = VERIFICATION_EFFORT.ref
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
# Errata-applied solve outcomes (``correctness@10`` ``*_errata`` metrics): the same rules
# over the cases the problem's pinned errata keeps. Primary only when the experiment
# pre-registers one (``analysis.primary_outcome``), and then always reported beside its
# raw counterpart (``RAW_COUNTERPART``); ``not_applicable`` for a problem without an errata.
SOLVE_RATE_ERRATA = "solve_rate_errata"
REACHED_ERRATA = "reached_errata"
FINAL_SOLVED_ERRATA = "final_solved_errata"
# Solvable-set solve outcomes (``correctness@10`` ``*_solvable`` metrics): the same rules over
# the cases the reference solution passes in every validation repeat. The default primary;
# ``unavailable`` (never zero) for a problem whose lock pins no solvable set.
SOLVE_RATE_SOLVABLE = "solve_rate_solvable"
REACHED_SOLVABLE = "reached_solvable"
FINAL_SOLVED_SOLVABLE = "final_solved_solvable"
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
    MetricDef(
        SOLVE_RATE_SOLVABLE,
        "Reference-solvable SCB success (primary; raw SCB success reported beside it)",
        View.CONTINUE,
        declared_direction(CORRECTNESS, "solved_checkpoints_solvable"),
        0.125,
    ),
    MetricDef(
        REACHED_SOLVABLE,
        "Reference-solvable SCB checkpoints reached (strict)",
        View.STRICT,
        declared_direction(CORRECTNESS, REACHED_SOLVABLE),
        1.0,
    ),
    MetricDef(
        FINAL_SOLVED_SOLVABLE,
        "Reference-solvable SCB final checkpoint success",
        View.CONTINUE,
        declared_direction(CORRECTNESS, "solved_solvable"),
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
"""Verification-effort outcomes are ``gate.<verification-effort trajectory metric>``."""
_GATE_OUTCOMES: tuple[tuple[str, str, float], ...] = (
    ("first_pass_clean_rate", "first-pass clean rate (decided steps)", 0.1),
    ("clean_at_step_end_rate", "clean at step end rate (decided steps)", 0.1),
    ("unresolved_rate", "unresolved rate (last seal refused)", 0.1),
    ("gate_error_rate", "gate error rate (validity: expected 0)", 0.05),
    ("mean_rounds_to_clean", "mean fix rounds to clean (clean steps)", 0.5),
    ("total_fix_rounds", "fix rounds per trial", 1.0),
    ("total_first_run_blocking", "failed blocking checks of the first seals per trial", 2.0),
    ("total_blocking_checks", "failed blocking checks over every decided seal per trial", 2.0),
    ("total_fix_input_tokens", "fix-turn input tokens per trial", 10_000.0),
    ("total_fix_output_tokens", "fix-turn output tokens per trial", 1_000.0),
    ("total_fix_cost_usd", "fix-turn cost per trial (USD)", 0.01),
    ("total_fix_wall_s", "fix-turn wall time per trial (s)", 60.0),
    ("total_gate_wall_s", "gate-seal wall time per trial (s)", 5.0),
    (
        "fix_round_correctness_delta",
        "fix-round correctness delta: final minus pre-fix case pass rate",
        0.05,
    ),
)
GATE_METRICS: tuple[MetricDef, ...] = tuple(
    MetricDef(
        f"{GATE_PREFIX}{metric}",
        f"Verification effort: {label}",
        View.GATE,
        declared_direction(VERIFICATION_EFFORT, metric),
        min_effect,
    )
    for metric, label, min_effect in _GATE_OUTCOMES
)
"""Verification-effort outcomes: compared only when the lock declares ``verification-effort``."""


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

K_VALUED = frozenset(
    {
        SOLVE_RATE,
        REACHED,
        SOLVE_RATE_ERRATA,
        REACHED_ERRATA,
        SOLVE_RATE_SOLVABLE,
        REACHED_SOLVABLE,
    }
)
"""Outcomes counted over the K bound checkpoints: their support is {0, 1/K, ..., 1} (or
{0..K}), so it is binary exactly when K is 1."""


def declared_kind(metric: MetricDef, checkpoints: int) -> Kind:
    """The outcome's declared type on a problem bound to ``checkpoints`` checkpoints.

    From the metric's definition and the bound checkpoint count only, never from observed
    values: final-checkpoint outcomes are binary, K-counted outcomes are binary when K is 1,
    everything else is continuous.
    """
    if metric.kind is Kind.BINARY or (metric.id in K_VALUED and checkpoints == 1):
        return Kind.BINARY
    return Kind.CONTINUOUS


def declared_outcome_types(bound: Mapping[str, int]) -> dict[str, object]:
    """The lock's ``analysis.outcome_types`` for problems bound to ``{ref: checkpoints}``.

    Per problem, the bound checkpoint count and the outcomes declared binary there (every
    other compared outcome is declared continuous). The cross-problem aggregate is the mean
    of per-problem means, whose support is never {0, 1}, so no outcome is binary there.
    """
    return {
        "problems": {
            ref: {
                "checkpoints": count,
                "binary": [
                    metric.id
                    for metric in ALL_METRICS
                    if declared_kind(metric, count) is Kind.BINARY
                ],
            }
            for ref, count in sorted(bound.items())
        },
        "aggregate": {"binary": []},
    }


VERIFIER_PREFIX = "verifier."
"""Verifier diagnostics: described per arm, never compared (no verdict, no effect)."""
VERIFIER_DISAGREEMENTS = f"{VERIFIER_PREFIX}grading_coverage_disagreements"
VERIFIER_DISAGREEMENT_SOURCE = "code-quality@9"
VERIFIER_METRICS: tuple[MetricDef, ...] = (
    MetricDef(
        VERIFIER_DISAGREEMENTS,
        "Verifier: grader cases whose outcome differed between the grading run and the "
        "coverage run, summed over a trial's transitions (code-quality@9 "
        "regression_parity_excluded_cases; diagnostic, never an outcome)",
        View.QUALITY,
        Direction.NEUTRAL,
        1.0,
    ),
)

TOTAL_COST = "total_cost_usd"
COST_METRICS = ("total_cost_usd", "total_tokens", "total_turns", "total_agent_wall_s")


RAW_COUNTERPART = {
    SOLVE_RATE_SOLVABLE: SOLVE_RATE,
    REACHED_SOLVABLE: REACHED,
    FINAL_SOLVED_SOLVABLE: FINAL_SOLVED,
    SOLVE_RATE_ERRATA: SOLVE_RATE,
    REACHED_ERRATA: REACHED,
    FINAL_SOLVED_ERRATA: FINAL_SOLVED,
}
"""The raw SCB outcome a solvable-set or errata-applied primary is always reported beside."""
PRIMARY_FROM_LOCK = "lock (analysis.primary_outcome)"
PRIMARY_DEFAULT = "default for {progression} progression"


def primary_metric(progression: str, configured: str | None = None) -> str:
    """The pre-registered primary outcome, else the solvable-set solve rate."""
    if configured is not None:
        return outcome_metric(configured).id
    if progression in ("continue", "halt_on_failure"):
        return SOLVE_RATE_SOLVABLE
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
