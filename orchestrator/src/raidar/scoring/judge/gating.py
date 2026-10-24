"""Keep shadow Jev observations out of Metrics and verdicts (plan H2).

A scorer that emits a Jev-tier metric records, in
``AssessResult.provenance.policy_modes``, the mode of the policy that produced
it, keyed by the metric id (``jev_policy_modes`` builds that map). Analysis and
Metric inputs must read observations through ``verdict_observations``:

- deterministic and LLM-tier observations pass unchanged;
- a Jev-tier observation passes only when its metric's recorded mode is
  ``enforce``. A shadow, off or unrecorded mode excludes it (fail closed).

Excluded observations are still stored and shown, labelled uncalibrated.
"""

from __future__ import annotations

from collections.abc import Mapping

from raidar.scoring.framework.manifest import ScorerManifest, Tier
from raidar.scoring.framework.result import AssessResult, Observation
from raidar.scoring.judge.policies import PolicyMode, PolicyRegistry


def jev_policy_modes(
    metric_policies: Mapping[str, str], registry: PolicyRegistry
) -> dict[str, str]:
    """``{metric id: effective policy mode}`` for a scorer's Jev-tier metrics."""
    return {
        metric: registry.get(policy_id).mode.value for metric, policy_id in metric_policies.items()
    }


def counts_toward_verdicts(manifest: ScorerManifest, result: AssessResult, metric_id: str) -> bool:
    metric = manifest.metric(metric_id)
    if metric is None:
        return False
    if metric.tier is not Tier.JEV:
        return True
    return result.provenance.policy_modes.get(metric_id) == PolicyMode.ENFORCE.value


def verdict_observations(manifest: ScorerManifest, result: AssessResult) -> tuple[Observation, ...]:
    """The observations that may feed Metrics and verdicts."""
    return tuple(
        item
        for item in result.observations
        if counts_toward_verdicts(manifest, result, item.metric)
    )


def uncalibrated_observations(
    manifest: ScorerManifest, result: AssessResult
) -> tuple[Observation, ...]:
    """Jev-tier observations shown as uncalibrated and excluded from verdicts."""
    return tuple(
        item
        for item in result.observations
        if not counts_toward_verdicts(manifest, result, item.metric)
    )
