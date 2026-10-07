"""``gate-effort@9``: gate-effort@8 plus the autofix step and the fix cost by refusal.

``gate-effort@8`` (``raidar.scorers.gate_effort_v8``) is released and
unchanged. Iteration 4 of the guardrails experiment adds guardrail-gate@4's
autofix step (the pack's safe auto-fixers before each enforced check; on a
throwaway copy beside a shadow check). @9 keeps every @8 metric, value,
status, reason and evidence item and adds (``manifest``):

- the autofix passes per step (``autofix_values``): completed passes
  (``autofix_rounds``), typed failures (``autofix_errors``), files and lines
  changed, and the block-tier findings they fixed, in total and by family;
- the counts after autofix and before it, so an enforced arm (whose gate runs
  see the fixed tree) and a shadow arm (whose check sees the raw one) compare
  like for like: ``first_pass_clean_post_autofix`` (and its rate),
  ``first_run_blocking_post_autofix_net``, ``step_end_blocking_post_autofix_net``
  and ``first_run_blocking_raw_net``, each with its ``new_*`` count (the first
  one minus the previous gated step's end; the raw one against @8's step-end
  count) and trajectory totals;
- each fix session's cost and wall time by the kind of refusal that started it
  (``refusals``), and the sessions whose refusal included the coverage minimum.

A shadow gate's fix-side values are not applicable, as in @2 on; a step with no
source change is not applicable to first-pass clean, as in @3 on.
"""

from __future__ import annotations

from pathlib import Path

from raidar.scorers.gate_effort_v8.scorer import GateEffortV8Scorer
from raidar.scorers.gate_effort_v9.assess import assess_trajectory
from raidar.scorers.gate_effort_v9.manifest import GATE_EFFORT_V9
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.sdk.manifest import ScorerManifest
from raidar.scorers.sdk.result import AssessResult


class GateEffortV9Scorer(GateEffortV8Scorer):
    """gate-effort@8 plus guardrail-gate@4's autofix step and the fix cost by refusal."""

    @property
    def manifest(self) -> ScorerManifest:
        return GATE_EFFORT_V9

    def assess(self, ctx: AssessContext) -> AssessResult:
        result, self._missing = assess_trajectory(ctx, self._reader())
        return result


def gate_effort_v9_scorer() -> GateEffortV9Scorer:
    """Registry factory for ``gate-effort@9`` without a store (pre-fix grades unavailable)."""
    return GateEffortV9Scorer()


def gate_effort_v9_scorer_for_store(
    store_root: Path, *, replay_only: bool = False, trial: str | None = None
) -> GateEffortV9Scorer:
    """Store-bound factory: reads pre-fix grades from the Assessment store."""
    del replay_only, trial
    return GateEffortV9Scorer(store_root)


__all__ = [
    "GATE_EFFORT_V9",
    "GateEffortV9Scorer",
    "gate_effort_v9_scorer",
    "gate_effort_v9_scorer_for_store",
]
