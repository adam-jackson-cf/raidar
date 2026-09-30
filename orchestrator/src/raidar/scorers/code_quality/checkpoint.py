"""Measure one checkpoint snapshot: V/E/H evidence and the lineage symbols R/G need.

The assessor-owned inventory (plan F3) decides which files are production;
the pinned tool environment measures the snapshot once (file rows, symbol
rows, call graph), and the ported producers turn that into the fork's typed
V/E and H evidence. Any reason a component cannot be measured is kept as a
typed eligibility code (the fork's ``production._reason``), never a zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.scorers.code_quality.architecture import (
    ProductionGraphError,
    ProductionGraphResult,
    produce_production_graph,
)
from raidar.scorers.code_quality.diffs import production_symbols
from raidar.scorers.code_quality.lineage_models import SymbolIdentity
from raidar.scorers.code_quality.measurement import MeasurementError, ToolEnvironment
from raidar.scorers.code_quality.models import ELIGIBILITY_CODES
from raidar.scorers.code_quality.quality import (
    ProductionQualityError,
    ProductionQualityResult,
    produce_production_quality,
)
from raidar.scorers.inventory import FileRole, Inventory
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.result import AssessStatus

INVALID_EVIDENCE = "score_evidence_invalid"
UNSUPPORTED_LANGUAGE = "production_language_unsupported"
ENTRY_FILE_MISSING = "scorer config entry_file is required to build the call graph for H"
ENTRY_FILE_ABSENT = "entry_file_absent"


@dataclass(frozen=True)
class Gap:
    """Why a value is missing: its status and a reason starting with a code."""

    status: AssessStatus
    reason: str


def eligibility_code(error: Exception) -> str:
    """The fork's eligibility code for a producer error (``production._reason``)."""
    candidate = str(error).split(":", maxsplit=1)[0]
    return candidate if candidate in ELIGIBILITY_CODES else INVALID_EVIDENCE


def _ineligible(error: Exception) -> Gap:
    code = eligibility_code(error)
    message = str(error)
    return Gap(
        AssessStatus.INELIGIBLE, message if message.startswith(code) else f"{code}: {message}"
    )


@dataclass(frozen=True)
class Measured:
    """A snapshot the tools measured, with its V/E and H results (or their gaps)."""

    snapshot: Path
    production_paths: tuple[str, ...]
    symbols: tuple[SymbolIdentity, ...]
    quality: ProductionQualityResult | Gap
    graph: ProductionGraphResult | Gap


@dataclass(frozen=True)
class CheckpointMeasure:
    """Everything measured for one checkpoint, or the gap that prevented it."""

    step: StepInputs
    outcome: Measured | Gap
    inventory: Inventory | None = None

    def quality(self) -> tuple[ProductionQualityResult, Path] | Gap:
        if isinstance(self.outcome, Gap):
            return self.outcome
        if isinstance(self.outcome.quality, Gap):
            return self.outcome.quality
        return self.outcome.quality, self.outcome.snapshot

    def graph(self) -> tuple[ProductionGraphResult, Path] | Gap:
        if isinstance(self.outcome, Gap):
            return self.outcome
        if isinstance(self.outcome.graph, Gap):
            return self.outcome.graph
        return self.outcome.graph, self.outcome.snapshot


def _unsupported(inventory: Inventory) -> list[str]:
    """Production files in a language this Python scorer cannot measure (fork inventory)."""
    return [
        item.path
        for item in inventory.files
        if item.role is FileRole.PRODUCTION and item.extension != ".py"
    ]


def _step_gap(step: StepInputs, inventory: Inventory | None) -> Gap | None:
    if not step.reached:
        return Gap(AssessStatus.NOT_APPLICABLE, "checkpoint not reached")
    if step.snapshot is None or inventory is None:
        return Gap(AssessStatus.UNAVAILABLE, "no stored snapshot for this step")
    if not inventory.eligible:
        details = "; ".join(f"{issue.path}: {issue.detail}" for issue in inventory.invalid)
        return Gap(AssessStatus.INELIGIBLE, f"{INVALID_EVIDENCE}: {details}")
    unsupported = _unsupported(inventory)
    if unsupported:
        return Gap(
            AssessStatus.INELIGIBLE,
            f"{UNSUPPORTED_LANGUAGE}: non-Python production files {', '.join(unsupported)}",
        )
    return None


def _entry_gap(snapshot: Path, entry_file: str | None) -> Gap | None:
    """Why the call graph cannot be built from this snapshot's configured entrypoint.

    The fork builds an empty graph (H = 1, the best score) when the entrypoint
    is not in the snapshot, for example after an agent renamed or deleted it;
    here that is an unmeasured H, never a value (F6).
    """
    if entry_file is None:
        return Gap(AssessStatus.UNAVAILABLE, ENTRY_FILE_MISSING)
    root = snapshot.resolve()
    entry = (root / entry_file).resolve()
    if not entry.is_relative_to(root) or not entry.is_file():
        return Gap(
            AssessStatus.INELIGIBLE,
            f"{ENTRY_FILE_ABSENT}: configured entry_file {entry_file!r} is not a file "
            "in the snapshot",
        )
    return None


def _graph(
    checkpoint: str, inventory: Inventory, traversal: object, entry_gap: Gap | None
) -> ProductionGraphResult | Gap:
    if entry_gap is not None:
        return entry_gap
    if not isinstance(traversal, Mapping):
        return Gap(AssessStatus.UNAVAILABLE, ENTRY_FILE_MISSING)
    try:
        return produce_production_graph(checkpoint, inventory.files, traversal)
    except (ProductionGraphError, ValueError) as error:
        return _ineligible(error)


def _measured(
    step: StepInputs,
    snapshot: Path,
    inventory: Inventory,
    rows: Mapping[str, Any],
    tools: ToolEnvironment,
    entry_gap: Gap | None,
) -> Measured:
    production = tuple(item.path for item in inventory.files if item.role is FileRole.PRODUCTION)
    quality: ProductionQualityResult | Gap
    try:
        quality = produce_production_quality(
            step.checkpoint, snapshot, inventory.files, rows["files"], rows["symbols"], tools
        )
    except (ProductionQualityError, ValueError) as error:
        quality = _ineligible(error)
    return Measured(
        snapshot=snapshot,
        production_paths=production,
        symbols=production_symbols(rows["symbols"], production),
        quality=quality,
        graph=_graph(step.checkpoint, inventory, rows.get("graph"), entry_gap),
    )


def measure_checkpoint(
    ctx: AssessContext, step: StepInputs, tools: ToolEnvironment, entry_file: str | None
) -> CheckpointMeasure:
    """Measure one step's snapshot; failures become typed gaps, never values."""
    inventory = ctx.inventory(step.step) if step.reached else None
    gap = _step_gap(step, inventory)
    if gap is not None or step.snapshot is None or inventory is None:
        return CheckpointMeasure(
            step, gap or Gap(AssessStatus.UNAVAILABLE, "no snapshot"), inventory
        )
    entry_gap = _entry_gap(step.snapshot, entry_file)
    try:
        rows = tools.measure(step.snapshot, entry_file if entry_gap is None else None)
    except MeasurementError as error:
        return CheckpointMeasure(step, _ineligible(error), inventory)
    return CheckpointMeasure(
        step, _measured(step, step.snapshot, inventory, rows, tools, entry_gap), inventory
    )
