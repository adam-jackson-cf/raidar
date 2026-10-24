"""Headline-policy comparability across experiments that the surface compares.

The surface compares every experiment record of one ``problem``: it ranks their
composite means, plots them on one scatter and moves between arms. A composite
is only comparable under one headline metric, so when a compared family mixes
policies every record in it carries a ``headline-policy-mismatch`` issue
(shown in the comparison table's issue count and the row's findings) and every
condition diff in it carries the same warning (shown on the arm-movement card).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from raidar.contracts.review_contract import ArmDiffArtifact, ReviewExperimentRecord
from raidar.evaluation.review.projection.notes import EvidenceRef, Finding

UNKNOWN_POLICY = "unrecorded headline metric"
MISMATCH_CATEGORY = "headline-policy-mismatch"


def headline_policy(record: ReviewExperimentRecord) -> str:
    policy = record.aggregate.get("headline_policy")
    return policy if isinstance(policy, str) else UNKNOWN_POLICY


def _warning(policies: dict[str, list[str]]) -> str:
    listed = ", ".join(f"{policy} ({len(ids)})" for policy, ids in sorted(policies.items()))
    return f"headline metrics differ across compared experiments: {listed}"


def _finding(policies: dict[str, list[str]]) -> dict[str, object]:
    return Finding(
        id=MISMATCH_CATEGORY,
        kind="issue",
        category=MISMATCH_CATEGORY,
        title="Headline metrics differ in this comparison",
        detail=(
            _warning(policies)
            + "; composite scores under different policies are not comparable, so compare "
            "within one policy"
        ),
        span=None,
        evidence=tuple(
            EvidenceRef("headline_policy", policy, ", ".join(sorted(ids)))
            for policy, ids in sorted(policies.items())
        ),
    ).as_json()


def mark_policy_mismatches(
    experiments: Sequence[ReviewExperimentRecord], diffs: Sequence[ArmDiffArtifact]
) -> tuple[list[ReviewExperimentRecord], list[ArmDiffArtifact]]:
    """Flag every compared family (same ``problem``) whose records use several policies."""
    families: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for record in experiments:
        families[record.problem][headline_policy(record)].append(record.experiment_id)
    mixed = {family: dict(policies) for family, policies in families.items() if len(policies) > 1}
    marked = [
        record.model_copy(update={"findings": [*record.findings, _finding(mixed[record.problem])]})
        if record.problem in mixed
        else record
        for record in experiments
    ]
    warned = [
        diff.model_copy(
            update={
                "comparable_warnings": [*diff.comparable_warnings, _warning(mixed[diff.problem])]
            }
        )
        if diff.problem in mixed
        else diff
        for diff in diffs
    ]
    return marked, warned
