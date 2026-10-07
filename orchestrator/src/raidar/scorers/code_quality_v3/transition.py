"""``code-quality@3``'s G run evidence: @2's, with a case-level parity exclusion.

A step's two runs come from the trial's own coverage pass (its record's parity
verdict and ``g-kernel-input.json``, as ``code-quality@1`` reads them) or from
the store's offline run (as ``code-quality@2`` reads it). When parity held,
they are used as @2 uses them. When it failed only because a few cases changed
outcome, ``parity.reconcile`` leaves those cases out of both runs; otherwise the
step is ``ineligible`` exactly as in @2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from raidar.scorers.code_quality.checkpoint import Gap
from raidar.scorers.code_quality.lineage_models import RegressionRunEvidence
from raidar.scorers.code_quality.regression import PARITY_UNAVAILABLE
from raidar.scorers.code_quality.transition import PARITY_FAILED, verifier_record_dir
from raidar.scorers.code_quality_v2.transition import (
    OfflineCoverageReader,
    RunEvidences,
)
from raidar.scorers.code_quality_v3.parity import reconcile
from raidar.scorers.sdk.context import StepInputs
from raidar.scorers.sdk.result import AssessStatus
from raidar.verifier.coverage_store import read_offline_coverage
from raidar.verifier.models import ParityVerdict
from raidar.verifier.records import FORK_EVIDENCE_FILE

_Source = tuple[ParityVerdict | None, dict[str, Any] | None]


def _in_trial_fork(step: StepInputs) -> dict[str, Any] | None:
    directory = verifier_record_dir(step)
    path = directory / FORK_EVIDENCE_FILE if directory is not None else None
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _runs(fork: dict[str, Any]) -> RunEvidences | Gap:
    try:
        return (
            RegressionRunEvidence.model_validate(fork["baseline"]),
            RegressionRunEvidence.model_validate(fork["produced"]),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        return Gap(AssessStatus.INELIGIBLE, f"{PARITY_UNAVAILABLE}: {error}")


@dataclass
class CaseLevelCoverageReader(OfflineCoverageReader):
    """@2's reader whose parity failure from a few flipped cases leaves those cases out."""

    excluded: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def _source(self, step: StepInputs) -> _Source | None:
        """The failed parity verdict and G input behind a step, or None when @2 decides it."""
        record = step.record
        if record is None:
            return None
        if record.coverage.enabled:
            return record.coverage.parity, _in_trial_fork(step)
        if self.store_root is None:
            return None
        stored = read_offline_coverage(
            self.store_root, record.bundle.digest, record.step, record.snapshot.digest
        )
        return (stored.parity, stored.fork) if stored is not None else None

    def run_evidence(self, step: StepInputs) -> RunEvidences | Gap:
        found = super().run_evidence(step)
        if not isinstance(found, Gap):
            self.excluded[step.step] = ()
            return found
        if not found.reason.startswith(f"{PARITY_FAILED}:"):
            return found
        source = self._source(step)
        if source is None or source[0] is None or source[1] is None:
            return found
        runs = _runs(source[1])
        if isinstance(runs, Gap):
            return found
        settled = reconcile(source[0].reasons, *runs)
        if settled is None:
            return found
        baseline, produced, flipped = settled
        self.excluded[step.step] = flipped
        return baseline, produced


__all__ = ["CaseLevelCoverageReader"]
