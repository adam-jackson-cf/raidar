"""The synthetic Quality fixture's lock: only what the Quality view reads from a lock.

It is sealed like a real lock (``seal_lock``: section digests, ``lock_sha256``
over its execution section, ``document_sha256``), as ``read_lock`` checks, and
it sits where ``default_lock_path`` looks for it beside the experiment file.
"""

from __future__ import annotations

import json
from pathlib import Path

from raidar.spec.lock import LOCK_VERSION, default_lock_path, seal_lock
from raidar.spec.policies import DEFAULT_POLICY_REGISTRY
from raidar.spec.schema import DEFAULT_ALPHA, DEFAULT_MAX_EXCLUSION_FRACTION, DEFAULT_MIN_PAIRS

PINNED_THRESHOLDS = {
    "min_pairs": DEFAULT_MIN_PAIRS,
    "max_exclusion_fraction": DEFAULT_MAX_EXCLUSION_FRACTION,
    "alpha": DEFAULT_ALPHA,
    "min_effects": {},
}
"""The verdict thresholds a real lock pins (``AnalysisSpec`` defaults)."""


def default_headline_policy(checkpoints: int) -> str:
    return DEFAULT_POLICY_REGISTRY.default_for_checkpoints(checkpoints).ref


def write_synthetic_lock(
    experiment_file: Path, *, experiment: str, baseline_arm: str, headline_policy: str
) -> str:
    """Write the lock beside ``experiment_file`` and return its ``lock_sha256``."""
    analysis = {"baseline_arm": baseline_arm, **PINNED_THRESHOLDS}
    lock = seal_lock(
        {
            "lock_version": LOCK_VERSION,
            "synthetic": True,
            "experiment": {"id": experiment},
            "spec": {"id": experiment, "analysis": analysis},
            "execution": {"design": {"id": experiment}},
            "assessment": {"headline_policy": {"ref": headline_policy}},
            "analysis": analysis,
            "toolchain": {},
        }
    )
    default_lock_path(experiment_file).write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return str(lock["lock_sha256"])
