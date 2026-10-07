"""``code-quality@3``'s G run evidence: @2's, with a case-level parity exclusion.

A step's two runs come from the trial's own coverage pass (its record's parity
verdict and ``g-kernel-input.json``, as ``code-quality@1`` reads them) or from
the store's offline run (as ``code-quality@2`` reads it). When parity held,
they are used as @2 uses them. When it failed only because a few cases changed
outcome, ``parity.reconcile`` leaves those cases out of both runs; otherwise the
step is ``ineligible`` exactly as in @2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from raidar.scorers.code_quality.checkpoint import Gap
from raidar.scorers.code_quality.transition import PARITY_FAILED
from raidar.scorers.code_quality_v2.transition import (
    OfflineCoverageReader,
    RunEvidences,
)
from raidar.scorers.code_quality_v3.parity import fork_runs, reconcile
from raidar.scorers.code_quality_v3.sources import parity_source
from raidar.scorers.sdk.context import StepInputs


@dataclass
class CaseLevelCoverageReader(OfflineCoverageReader):
    """@2's reader whose parity failure from a few flipped cases leaves those cases out."""

    excluded: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def run_evidence(self, step: StepInputs) -> RunEvidences | Gap:
        found = super().run_evidence(step)
        if not isinstance(found, Gap):
            self.excluded[step.step] = ()
            return found
        if not found.reason.startswith(f"{PARITY_FAILED}:"):
            return found
        source = parity_source(step, self.store_root)
        if source is None or source[0] is None or source[1] is None:
            return found
        runs = fork_runs(source[1])
        if runs is None:
            return found
        settled = reconcile(source[0].reasons, *runs)
        if settled is None:
            return found
        baseline, produced, flipped = settled
        self.excluded[step.step] = flipped
        return baseline, produced


__all__ = ["CaseLevelCoverageReader"]
