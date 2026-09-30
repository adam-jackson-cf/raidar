"""The synthetic Quality fixture's lock: only what the Quality view reads from a lock.

Its ``lock_sha256`` is the digest of its body, as ``read_lock`` checks, and it
sits where ``default_lock_path`` looks for it beside the experiment file.
"""

from __future__ import annotations

import json
from pathlib import Path

from raidar.spec.canonical import digest_json
from raidar.spec.lock import default_lock_path
from raidar.spec.policies import DEFAULT_POLICY_REGISTRY


def default_headline_policy(checkpoints: int) -> str:
    return DEFAULT_POLICY_REGISTRY.default_for_checkpoints(checkpoints).ref


def write_synthetic_lock(
    experiment_file: Path, *, experiment: str, baseline_arm: str, headline_policy: str
) -> str:
    """Write the lock beside ``experiment_file`` and return its ``lock_sha256``."""
    body = {
        "synthetic": True,
        "experiment": {"id": experiment},
        "spec": {"id": experiment, "analysis": {"baseline_arm": baseline_arm}},
        "headline_policy": {"ref": headline_policy},
    }
    digest = digest_json(body)
    default_lock_path(experiment_file).write_text(
        json.dumps({**body, "lock_sha256": digest}, indent=2), encoding="utf-8"
    )
    return digest
