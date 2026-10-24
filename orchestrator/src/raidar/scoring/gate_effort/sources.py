"""The steps that changed no source file: their gate's first run checked nothing new."""

from __future__ import annotations

from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.inventory import FileRole, Inventory, build_inventory

NO_SOURCE_CHANGE = (
    "no source change in the step: its production and test files equal the previous "
    "snapshot's, so the gate's first run checked nothing new"
)
_SOURCE_ROLES = (FileRole.PRODUCTION, FileRole.TEST)

SourceMap = dict[str, str | None]


def _sources(inventory: Inventory | None) -> SourceMap | None:
    if inventory is None:
        return None
    return {item.path: item.sha256 for item in inventory.files if item.role in _SOURCE_ROLES}


def _step_sources(ctx: AssessContext, name: str) -> SourceMap | None:
    try:
        return _sources(ctx.inventory(name))
    except (OSError, NotADirectoryError):
        return None


def _baseline_sources(ctx: AssessContext, name: str) -> SourceMap | None:
    """The source files before ``name`` ran: the prior step's, or the starter's."""
    prior = ctx.prior(name)
    if prior is not None:
        return _step_sources(ctx, prior.step) if prior.reached else None
    if ctx.starter is None:
        return None
    try:
        return _sources(build_inventory(ctx.starter, ctx.inventory_policy))
    except (OSError, NotADirectoryError):
        return None


def unchanged_steps(ctx: AssessContext) -> frozenset[str]:
    """Reached steps whose production and test files equal the previous snapshot's."""
    unchanged: set[str] = set()
    for inputs in ctx.steps:
        if not inputs.reached:
            continue
        before = _baseline_sources(ctx, inputs.step)
        after = _step_sources(ctx, inputs.step) if before is not None else None
        if after is not None and after == before:
            unchanged.add(inputs.step)
    return frozenset(unchanged)
