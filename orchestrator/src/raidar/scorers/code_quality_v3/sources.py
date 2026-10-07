"""Where a step's failed parity verdict and G input come from: the trial's pass or the store.

The trial's own coverage pass keeps them in the step record (``coverage.parity``)
and ``g-kernel-input.json`` beside it, as ``code-quality@1`` reads them; a step
whose trial deferred the pass keeps them in the store's offline run, as
``code-quality@2`` reads it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from raidar.scorers.code_quality.transition import verifier_record_dir
from raidar.scorers.sdk.context import StepInputs
from raidar.verifier.coverage_store import read_offline_coverage
from raidar.verifier.models import ParityVerdict
from raidar.verifier.records import FORK_EVIDENCE_FILE

ParitySource = tuple[ParityVerdict | None, dict[str, Any] | None]


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


def parity_source(step: StepInputs, store_root: Path | None) -> ParitySource | None:
    """The parity verdict and G input behind a step, or None when there is none to read."""
    record = step.record
    if record is None:
        return None
    if record.coverage.enabled:
        return record.coverage.parity, _in_trial_fork(step)
    if store_root is None:
        return None
    stored = read_offline_coverage(
        store_root, record.bundle.digest, record.step, record.snapshot.digest
    )
    return (stored.parity, stored.fork) if stored is not None else None


__all__ = ["ParitySource", "parity_source"]
