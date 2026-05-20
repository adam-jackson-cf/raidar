# Step 3 Workflow: Validate Baseline

## Objective

Validate the authored baseline before running it.

## Required actions

1. Run scenario validation.
2. Inspect `scorers[]`, scorer-level weights, scenario metric overrides, verification, acceptance, and visual config for consistency.
3. Confirm root baselines have `parent_revision: null`.
4. For matrix-backed baselines, confirm every matrix entry resolves to an existing `<matrix.scenario>/<scenario_revision>/scenario.yaml`.
5. Fix validation failures without weakening checks or scoring.

## Done when

- Scenario validation passes.
- Comparable contract is internally consistent.
- Stored matrix definitions resolve to the intended baseline revision when used.
- No validation issue was bypassed.
