"""``gate-effort@10``: gate-effort@9 plus autofix counts that compare across gate modes.

``gate-effort@9`` (``raidar.scorers.gate_effort_v9``) is released and
unchanged. Its per-step autofix counts (``autofix_lines_changed``,
``autofix_removed_blocking``) mean different things in the two gate modes: a
shadow gate's dry run re-fixes the whole never-fixed tree at every step, so its
sums are cumulative, while an enforced gate's first pass sees only the step's
new code (the 13.7 O3 problem of the blocking counts, found again for autofix
in the guardrails experiment's smoke-4, I5). @10 keeps every @9 metric, value,
status, reason and evidence item and adds ``new_autofix_lines_changed`` and
``new_autofix_removed_blocking`` (in total and by family): the step's first
pass minus what the previous gated step left for autofix (``new_autofix``),
with trajectory totals.
"""

from __future__ import annotations

from pathlib import Path

from raidar.scorers.gate_effort_v9.scorer import GateEffortV9Scorer
from raidar.scorers.gate_effort_v10.assess import assess_trajectory
from raidar.scorers.gate_effort_v10.manifest import GATE_EFFORT_V10
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.sdk.manifest import ScorerManifest
from raidar.scorers.sdk.result import AssessResult


class GateEffortV10Scorer(GateEffortV9Scorer):
    """gate-effort@9 plus the autofix counts each step's own work added."""

    @property
    def manifest(self) -> ScorerManifest:
        return GATE_EFFORT_V10

    def assess(self, ctx: AssessContext) -> AssessResult:
        result, self._missing = assess_trajectory(ctx, self._reader())
        return result


def gate_effort_v10_scorer() -> GateEffortV10Scorer:
    """Registry factory for ``gate-effort@10`` without a store (pre-fix grades unavailable)."""
    return GateEffortV10Scorer()


def gate_effort_v10_scorer_for_store(
    store_root: Path, *, replay_only: bool = False, trial: str | None = None
) -> GateEffortV10Scorer:
    """Store-bound factory: reads pre-fix grades from the Assessment store."""
    del replay_only, trial
    return GateEffortV10Scorer(store_root)


__all__ = [
    "GATE_EFFORT_V10",
    "GateEffortV10Scorer",
    "gate_effort_v10_scorer",
    "gate_effort_v10_scorer_for_store",
]
