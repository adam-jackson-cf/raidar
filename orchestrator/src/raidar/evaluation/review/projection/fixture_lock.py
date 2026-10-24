"""The synthetic review fixture's experiment lock: sealed like a real lock.

Only what the review projection reads from a lock is filled in: the spec, the
locked conditions and problem (execution section), the headline metric chosen
by K (assessment section) and the analysis section. ``seal_lock`` adds the
section digests, ``lock_sha256`` (the execution digest) and ``document_sha256``,
so ``parse_lock`` accepts it exactly as it accepts a ``raidar lock`` output.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from raidar.design.experiments.canonical import digest_json
from raidar.design.experiments.lock import LOCK_VERSION, analysis_section, seal_lock
from raidar.design.experiments.metrics import DEFAULT_METRIC_REGISTRY


def fixture_lock(
    spec_json: Mapping[str, Any],
    *,
    problem: str,
    checkpoints: int,
    conditions: Mapping[str, Any],
) -> dict[str, Any]:
    return seal_lock(
        {
            "lock_version": LOCK_VERSION,
            "spec": spec_json,
            "execution": {
                "design": {"id": spec_json["id"]},
                "conditions": conditions,
                "problems": [
                    {"label": problem, "checkpoints": checkpoints, "digest": digest_json(problem)}
                ],
            },
            "assessment": {
                "metric": {
                    "ref": DEFAULT_METRIC_REGISTRY.default_for_checkpoints(checkpoints).ref,
                    "selection": "default_by_k",
                    "checkpoints": [checkpoints],
                },
            },
            "analysis": analysis_section(spec_json),
            "toolchain": {},
        }
    )
