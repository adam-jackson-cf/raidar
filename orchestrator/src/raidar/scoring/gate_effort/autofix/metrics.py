"""``gate-effort``'s autofix metrics: the autofix step and the fix cost by refusal.

The autofix passes (``guardrail-gate@4``), the counts after and before them, the fix sessions'
cost and wall time by the kind of refusal that started them, and the autofix counts that compare
across an enforced and a shadow gate (``new_autofix_*``). Ids and descriptions are part of the
scorer's identity (``gate_effort.manifest``).
"""

from __future__ import annotations

from raidar.scoring.framework.manifest import Direction, MetricLevel, MetricSpec, MetricType
from raidar.scoring.gate_effort.families import FAMILIES
from raidar.scoring.gate_effort.metrics import metric

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

_NET = "(no no-untyped-call cascade, a refused suppression counted once, as the *_net counts)"
_REFUSAL = (
    "split by the kind of refusal that started each session: its one rule family, "
    "coverage_minimum (TEST.coverage-minimum alone), mixed, or unknown; the kinds add up to "
    "the step's fix sessions"
)


def _counts(prefix: str, what: str, level: MetricLevel) -> tuple[MetricSpec, ...]:
    return tuple(
        metric(
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
        metric(
            f"{prefix}.{kind}",
            f"{what}, {kind} refusals ({_REFUSAL}).",
            level,
            MetricType.REAL,
            _LOWER,
            unit,
        )
        for kind in REFUSAL_KINDS
    )


AUTOFIX_STEP_METRICS: tuple[MetricSpec, ...] = (
    metric(
        AUTOFIX_ROUNDS,
        "Autofix passes the gate completed in the step (enforced: one "
        "before each gate run; shadow: one on the copy).",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    metric(
        AUTOFIX_ERRORS,
        "Autofix passes that could not complete (typed in the record; an "
        "enforced gate then ran that check without autofix).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        AUTOFIX_FILES,
        "Files the step's autofix passes changed (a shadow pass: would have).",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    metric(
        AUTOFIX_LINES,
        "Lines the step's autofix passes changed (added plus removed).",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    metric(
        AUTOFIX_REMOVED,
        "Block-tier findings the step's autofix passes fixed (Ruff code "
        "counts before minus after, FORMAT per file).",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    *_counts(AUTOFIX_REMOVED, "Block-tier findings the step's autofix passes fixed", _CHECKPOINT),
    metric(
        FIRST_PASS_POST,
        "The first check after autofix passed (enforced: the first gate "
        "run; shadow: the check of the fixed copy).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    metric(
        FIRST_RUN_POST,
        f"First-run blocking findings after autofix {_NET}.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        FIRST_RUN_RAW,
        f"First-run blocking findings before autofix {_NET} (enforced: "
        "the post-autofix count plus the first pass's removed block-tier findings; shadow: "
        "the raw check).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        STEP_END_POST,
        f"Blocking findings at the step's end after autofix {_NET} "
        "(enforced: the last decided gate run; shadow: the fixed copy's check).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        NEW_POST,
        "First-run blocking findings after autofix minus the previous gated "
        f"step's step-end count after autofix {_NET}.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
    ),
    metric(
        NEW_RAW,
        "First-run blocking findings before autofix minus the previous gated "
        f"step's step-end count {_NET}.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
    ),
    *_split(FIX_COST_BY_REFUSAL, "Fix-session cost", "usd", _CHECKPOINT),
    *_split(FIX_WALL_BY_REFUSAL, "Fix-session wall time", "s", _CHECKPOINT),
    metric(
        COVERAGE_SESSIONS,
        "Fix sessions whose refusal included TEST.coverage-minimum.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        COVERAGE_COST,
        "Cost of the fix sessions whose refusal included "
        "TEST.coverage-minimum (with any other code).",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
        "usd",
    ),
    metric(
        COVERAGE_WALL,
        "Wall time of the fix sessions whose refusal included "
        "TEST.coverage-minimum (with any other code).",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
        "s",
    ),
)
AUTOFIX_SUMMED = (
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
_STEP_BY_ID = {spec.id: spec for spec in AUTOFIX_STEP_METRICS}
AUTOFIX_TRAJECTORY_METRICS: tuple[MetricSpec, ...] = (
    *(
        metric(
            f"total_{name}",
            f"{_STEP_BY_ID[name].description} Summed over the gated steps.",
            _TRAJECTORY,
            _STEP_BY_ID[name].type,
            _STEP_BY_ID[name].direction,
            _STEP_BY_ID[name].unit,
        )
        for name in AUTOFIX_SUMMED
    ),
    metric(
        f"{FIRST_PASS_POST}_rate",
        "Share of decided steps whose first check after autofix passed.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    metric(
        f"final_{STEP_END_POST}",
        f"Blocking findings after autofix at the end of the last gated step {_NET}.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
)

NEW_LINES = "new_autofix_lines_changed"
NEW_REMOVED = "new_autofix_removed_blocking"
NEW_FAMILIES = tuple(f"{NEW_REMOVED}.{family}" for family in FAMILIES)
NEW_METRICS = (NEW_LINES, NEW_REMOVED, *NEW_FAMILIES)
"""The ``new_autofix_*`` step metrics; each has a ``total_<id>`` trajectory value."""

_NEW = (
    "the step's first autofix pass on its own work (enforced: the pass before gate run 1; "
    "shadow: the dry run on the copy) minus what the previous gated step left for autofix "
    "(enforced: 0 when the pass before its last gate run completed, the tree it handed on "
    "being already fixed; shadow: its dry run, since the shadow tree is never fixed); "
    "signed, as new_first_run_blocking. A shadow gate's per-step autofix "
    "counts re-count the whole never-fixed tree at every step, so their sums are cumulative; "
    "this form's sum telescopes to the final tree's count and compares with an enforced "
    "gate's"
)

NEW_AUTOFIX_STEP_METRICS: tuple[MetricSpec, ...] = (
    metric(
        NEW_LINES,
        f"Lines the autofix changed that the step's own work added: {_NEW}.",
        _CHECKPOINT,
        MetricType.REAL,
        _NEUTRAL,
    ),
    metric(
        NEW_REMOVED,
        f"Block-tier findings the autofix fixed that the step's own work added: {_NEW}.",
        _CHECKPOINT,
        MetricType.REAL,
        _NEUTRAL,
    ),
    *(
        metric(
            name,
            f"Block-tier findings the autofix fixed that the step's own work added, in the "
            f"{name.rsplit('.', 1)[1]} family ({NEW_REMOVED}'s rule).",
            _CHECKPOINT,
            MetricType.REAL,
            _NEUTRAL,
        )
        for name in NEW_FAMILIES
    ),
)
_NEW_BY_ID = {spec.id: spec for spec in NEW_AUTOFIX_STEP_METRICS}
NEW_AUTOFIX_TRAJECTORY_METRICS: tuple[MetricSpec, ...] = tuple(
    metric(
        f"total_{name}",
        f"{_NEW_BY_ID[name].description} Summed over the gated steps.",
        _TRAJECTORY,
        MetricType.REAL,
        _NEUTRAL,
    )
    for name in NEW_METRICS
)
