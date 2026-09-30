"""Headline-policy comparability across experiments that the surface compares.

The surface compares every experiment record of one ``scenario`` (a problem,
or a scenario-epoch scenario): it ranks their composite means, plots them on
one scatter and moves between revisions. A composite is only comparable under
one headline policy, so when a compared family mixes policies every record in
it carries a ``headline-policy-mismatch`` issue (shown in the comparison table's
issue count and the row's findings) and every condition diff in it carries the
same warning (shown on the revision-movement card).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from raidar.review_projection.notes import EvidenceRef, Finding
from raidar.schemas.review import ReviewExperimentRecord, RevisionDiffArtifact

SCENARIO_EPOCH_POLICY = "scenario-weighted composite (scenario epoch)"
MISMATCH_CATEGORY = "headline-policy-mismatch"


def headline_policy(record: ReviewExperimentRecord) -> str:
    policy = record.aggregate.get("headline_policy")
    return policy if isinstance(policy, str) else SCENARIO_EPOCH_POLICY


def _warning(policies: dict[str, list[str]]) -> str:
    listed = ", ".join(f"{policy} ({len(ids)})" for policy, ids in sorted(policies.items()))
    return f"headline policies differ across compared experiments: {listed}"


def _finding(policies: dict[str, list[str]]) -> dict[str, object]:
    return Finding(
        id=MISMATCH_CATEGORY,
        kind="issue",
        category=MISMATCH_CATEGORY,
        title="Headline policies differ in this comparison",
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
    experiments: Sequence[ReviewExperimentRecord], diffs: Sequence[RevisionDiffArtifact]
) -> tuple[list[ReviewExperimentRecord], list[RevisionDiffArtifact]]:
    """Flag every compared family (same ``scenario``) whose records use several policies."""
    families: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for record in experiments:
        families[record.scenario][headline_policy(record)].append(record.experiment_id)
    mixed = {family: dict(policies) for family, policies in families.items() if len(policies) > 1}
    marked = [
        record.model_copy(update={"findings": [*record.findings, _finding(mixed[record.scenario])]})
        if record.scenario in mixed
        else record
        for record in experiments
    ]
    warned = [
        diff.model_copy(
            update={
                "comparable_warnings": [*diff.comparable_warnings, _warning(mixed[diff.scenario])]
            }
        )
        if diff.scenario in mixed
        else diff
        for diff in diffs
    ]
    return marked, warned
