"""The one derivation of an Assessment's store key (plan F2).

``raidar assess --rescore`` writes Assessments under this key, and analysis and
the review projections find them by recomputing it, so every caller goes
through ``store_key``: scorer identity, the explicit assessor config, the
scorer's pinned pack tools and the trial's input digests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from raidar.scoring.framework.catalog import ScorerRegistration, tool_lock_for
from raidar.scoring.framework.store import ScorerIdentity, assessment_key


def scorer_identity(registration: ScorerRegistration) -> ScorerIdentity:
    return ScorerIdentity.model_validate(
        {
            "ref": registration.manifest.ref,
            "manifest_digest": registration.manifest.digest(),
            "implementation_digest": registration.implementation_digest(),
        }
    )


@dataclass(frozen=True)
class StoreKey:
    """Everything the key hashes, and the key; an ``Assessment`` records the same parts."""

    identity: ScorerIdentity
    config: dict[str, Any]
    tool_lock: dict[str, Any]
    inputs: dict[str, Any]
    key: str


def store_key(
    registration: ScorerRegistration,
    config: Mapping[str, Any],
    inputs: Mapping[str, Any],
    *,
    identity: ScorerIdentity | None = None,
) -> StoreKey:
    """The key for ``registration`` over ``inputs`` with explicit ``config``.

    ``identity`` lets a caller that looks up many trials reuse one computed
    ``scorer_identity`` (it hashes the implementation source).
    """
    identity = identity or scorer_identity(registration)
    explicit = dict(config)
    tool_lock: dict[str, Any] = dict(tool_lock_for(registration.manifest))
    hashed = dict(inputs)
    key = assessment_key(identity, config=explicit, tool_lock=tool_lock, inputs=hashed)
    return StoreKey(identity, explicit, tool_lock, hashed, key)
