"""``gate-effort``: what a commit gate cost the agent, step by step.

Read deterministically from each step's gate record (``raidar.scoring.gate_effort.records``);
no model is asked. Per step the scorer reports the gate's outcomes, its blocking findings by rule
family (a failed tests check counted once, with and without the typing cascade and a refused
suppression's duplicate), its tests check, the fix rounds' correctness, the autofix step and the
fix sessions' usage; per trajectory, rates over decided steps and totals over the gated steps.

The autofix counts of a shadow gate's dry run are cumulative (it re-fixes the whole never-fixed
tree at every step) while an enforced gate's first pass sees only the step's new code, so the
``new_autofix_*`` values subtract what the previous gated step left for autofix.
"""

from __future__ import annotations

from pathlib import Path

from raidar.grading.prefix_store import read_pre_fix_grade
from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.manifest import ScorerManifest
from raidar.scoring.framework.result import AssessResult
from raidar.scoring.gate_effort.assess import assess_trajectory
from raidar.scoring.gate_effort.grades import GradeReader
from raidar.scoring.gate_effort.manifest import GATE_EFFORT


class GateEffortScorer:
    """Deterministic scorer over each step's guardrail-gate record."""

    def __init__(self, store_root: Path | None = None) -> None:
        self._store_root = store_root
        self._missing: list[str] = []

    @property
    def manifest(self) -> ScorerManifest:
        return GATE_EFFORT

    def awaiting_answers(self) -> bool:
        """True when a pre-fix snapshot was not graded yet (a later rescore completes it)."""
        return bool(self._missing)

    def _reader(self) -> GradeReader | None:
        root = self._store_root
        if root is None:
            return None
        return lambda bundle, step, digest: read_pre_fix_grade(root, bundle, step, digest)

    def assess(self, ctx: AssessContext) -> AssessResult:
        result, self._missing = assess_trajectory(ctx, self._reader())
        return result


def gate_effort_scorer() -> GateEffortScorer:
    """Registry factory without a store (pre-fix grades unavailable)."""
    return GateEffortScorer()


def gate_effort_scorer_for_store(store_root: Path) -> GateEffortScorer:
    """Store-bound factory: reads pre-fix grades from the Assessment store."""
    return GateEffortScorer(store_root)


__all__ = ["GateEffortScorer", "gate_effort_scorer", "gate_effort_scorer_for_store"]
