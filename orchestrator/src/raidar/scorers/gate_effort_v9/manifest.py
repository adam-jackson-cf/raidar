"""``gate-effort@9``'s metrics: the autofix step and the fix-session cost by refusal.

Every ``gate-effort@8`` metric stays as it is; these are added (ids are part of
the scorer's identity).
"""

from __future__ import annotations

from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.gate_effort_v4.scorer import _metric
from raidar.scorers.gate_effort_v8.net import GATE_EFFORT_V8
from raidar.scorers.sdk.manifest import Direction, MetricLevel, MetricSpec, MetricType

_CHECKPOINT, _TRAJECTORY = MetricLevel.CHECKPOINT, MetricLevel.TRAJECTORY
_HIGHER, _LOWER, _NEUTRAL = Direction.HIGHER_BETTER, Direction.LOWER_BETTER, Direction.NEUTRAL

AUTOFIX_ROUNDS = "autofix_rounds"
AUTOFIX_ERRORS = "autofix_errors"
AUTOFIX_FILES = "autofix_files_changed"
AUTOFIX_LINES = "autofix_lines_changed"
AUTOFIX_REMOVED = "autofix_removed_blocking"
FIRST_PASS_POST = "first_pass_clean_post_autofix"
FIRST_RUN_POST = "first_run_blocking_post_autofix_net"
FIRST_RUN_RAW = "first_run_blocking_raw_net"
NEW_POST = "new_first_run_blocking_post_autofix_net"
NEW_RAW = "new_first_run_blocking_raw_net"
STEP_END_POST = "step_end_blocking_post_autofix_net"
FIX_COST_BY_REFUSAL = "fix_cost_usd_by_refusal"
FIX_WALL_BY_REFUSAL = "fix_wall_s_by_refusal"
COVERAGE_ONLY = "coverage_minimum"
MIXED = "mixed"
UNKNOWN = "unknown"
REFUSAL_KINDS = (*FAMILIES, COVERAGE_ONLY, MIXED, UNKNOWN)
"""A refusal's kind: its one family; ``coverage_minimum`` when ``TEST.coverage-minimum``
was its only blocking code (apart from the other ``tests`` refusals); ``mixed`` for more
than one family; ``unknown`` when its codes are not all listed (or it listed none)."""
COVERAGE_SESSIONS = "fix_sessions_with_coverage_minimum"
COVERAGE_COST = "fix_cost_usd_with_coverage_minimum"
COVERAGE_WALL = "fix_wall_s_with_coverage_minimum"

_NET = "(no no-untyped-call cascade, a refused suppression counted once, as gate-effort@8's *_net)"
_REFUSAL = (
    "split by the kind of refusal that started each session: its one rule family, "
    "coverage_minimum (TEST.coverage-minimum alone), mixed, or unknown; the kinds add up to "
    "the step's fix sessions"
)


def _counts(prefix: str, what: str, level: MetricLevel) -> tuple[MetricSpec, ...]:
    return tuple(
        _metric(
            f"{prefix}.{family}",
            f"{what} in the {family} family.",
            level,
            MetricType.COUNT,
            _NEUTRAL,
        )
        for family in FAMILIES
    )


def _split(prefix: str, what: str, unit: str, level: MetricLevel) -> tuple[MetricSpec, ...]:
    return tuple(
        _metric(
            f"{prefix}.{kind}",
            f"{what}, {kind} refusals ({_REFUSAL}).",
            level,
            MetricType.REAL,
            _LOWER,
            unit,
        )
        for kind in REFUSAL_KINDS
    )


V9_STEP_METRICS: tuple[MetricSpec, ...] = (
    _metric(
        AUTOFIX_ROUNDS,
        "Autofix passes the gate completed in the step (enforced: one "
        "before each gate run; shadow: one on the copy).",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    _metric(
        AUTOFIX_ERRORS,
        "Autofix passes that could not complete (typed in the record; an "
        "enforced gate then ran that check as guardrail-gate@3).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        AUTOFIX_FILES,
        "Files the step's autofix passes changed (a shadow pass: would have).",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    _metric(
        AUTOFIX_LINES,
        "Lines the step's autofix passes changed (added plus removed).",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    _metric(
        AUTOFIX_REMOVED,
        "Block-tier findings the step's autofix passes fixed (Ruff code "
        "counts before minus after, FORMAT per file).",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    *_counts(AUTOFIX_REMOVED, "Block-tier findings the step's autofix passes fixed", _CHECKPOINT),
    _metric(
        FIRST_PASS_POST,
        "The first check after autofix passed (enforced: the first gate "
        "run; shadow: the check of the fixed copy).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    _metric(
        FIRST_RUN_POST,
        f"First-run blocking findings after autofix {_NET}.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        FIRST_RUN_RAW,
        f"First-run blocking findings before autofix {_NET} (enforced: "
        "the post-autofix count plus the first pass's removed block-tier findings; shadow: "
        "the raw check).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        STEP_END_POST,
        f"Blocking findings at the step's end after autofix {_NET} "
        "(enforced: the last decided gate run; shadow: the fixed copy's check).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        NEW_POST,
        "First-run blocking findings after autofix minus the previous gated "
        f"step's step-end count after autofix {_NET}.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
    ),
    _metric(
        NEW_RAW,
        "First-run blocking findings before autofix minus the previous gated "
        f"step's step-end count {_NET}.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
    ),
    *_split(FIX_COST_BY_REFUSAL, "Fix-session cost", "usd", _CHECKPOINT),
    *_split(FIX_WALL_BY_REFUSAL, "Fix-session wall time", "s", _CHECKPOINT),
    _metric(
        COVERAGE_SESSIONS,
        "Fix sessions whose refusal included TEST.coverage-minimum.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        COVERAGE_COST,
        "Cost of the fix sessions whose refusal included "
        "TEST.coverage-minimum (with any other code).",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
        "usd",
    ),
    _metric(
        COVERAGE_WALL,
        "Wall time of the fix sessions whose refusal included "
        "TEST.coverage-minimum (with any other code).",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
        "s",
    ),
)
V9_SUMMED = (
    AUTOFIX_ROUNDS,
    AUTOFIX_ERRORS,
    AUTOFIX_FILES,
    AUTOFIX_LINES,
    AUTOFIX_REMOVED,
    *(f"{AUTOFIX_REMOVED}.{family}" for family in FAMILIES),
    NEW_POST,
    NEW_RAW,
    *(f"{FIX_COST_BY_REFUSAL}.{kind}" for kind in REFUSAL_KINDS),
    *(f"{FIX_WALL_BY_REFUSAL}.{kind}" for kind in REFUSAL_KINDS),
    COVERAGE_SESSIONS,
    COVERAGE_COST,
    COVERAGE_WALL,
)
"""Step metrics whose trajectory value is ``total_<id>`` (the sum over gated steps)."""
_STEP_BY_ID = {metric.id: metric for metric in V9_STEP_METRICS}
V9_TRAJECTORY_METRICS: tuple[MetricSpec, ...] = (
    *(
        _metric(
            f"total_{name}",
            f"{_STEP_BY_ID[name].description} Summed over the gated steps.",
            _TRAJECTORY,
            _STEP_BY_ID[name].type,
            _STEP_BY_ID[name].direction,
            _STEP_BY_ID[name].unit,
        )
        for name in V9_SUMMED
    ),
    _metric(
        f"{FIRST_PASS_POST}_rate",
        "Share of decided steps whose first check after autofix passed.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    _metric(
        f"final_{STEP_END_POST}",
        f"Blocking findings after autofix at the end of the last gated step {_NET}.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
)
GATE_EFFORT_V9 = GATE_EFFORT_V8.model_copy(
    update={
        "version": "9",
        "description": (
            f"{GATE_EFFORT_V8.description} Plus a guardrail-gate@4 autofix step's effect: "
            "passes, errors, files and lines changed, block-tier findings fixed by family, "
            "first-pass clean and blocking counts after autofix (and before, comparable "
            "across an enforced and a shadow gate), and the fix sessions' cost and wall time "
            "split by the kind of refusal that started them (coverage minimum apart)."
        ),
        "metrics": (*GATE_EFFORT_V8.metrics, *V9_STEP_METRICS, *V9_TRAJECTORY_METRICS),
    }
)

__all__ = [
    "GATE_EFFORT_V9",
    "REFUSAL_KINDS",
    "V9_STEP_METRICS",
    "V9_SUMMED",
    "V9_TRAJECTORY_METRICS",
]
