# Step 1 Workflow: Confirm Comparability

## Objective

Protect benchmark comparability across revisions.

## Required actions

1. Compare scorer ids, scorer versions, scorer weights, scenario metric overrides, verification, acceptance, and visual config to the baseline.
2. For matrix-backed comparisons, confirm `matrix.scenario`, `matrix.experiment`, nested AgentSpecs, and repeat settings remain comparable.
3. Treat matrix entry `id` and `scenario_revision` as the normal matrix delta for a new revision evidence point.
4. Stop if the scoring contract or intended matrix run shape must change.
5. Use a new baseline for scoring-contract changes.

## Done when

- Comparable contract is unchanged.
- Matrix-backed comparisons preserve the same scenario root, experiment settings, and AgentSpecs.
- Any scoring-contract change has been routed to baseline creation.
- Revision scope is limited to non-contract improvement surfaces.
