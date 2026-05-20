# Step 1 Workflow: Select Scoring Contract

## Objective

Choose the stable scoring contract for comparable revisions.

## Required actions

1. Select scorer ids, scorer versions, and scorer-level weights from the predetermined scorer catalog.
2. Confirm every scorer-derived metric is configured by the scenario or by its scorer definition.
3. State that changing scorer ids, scorer versions, scorer weights, scorer-owned metric weights, verification, acceptance, visual config, or scenario metric overrides starts a new baseline.

## Scorer selection

- Use `design-to-code` for visual design implementation scenarios.
- Use `code-delivery` for nonvisual implementation scenarios.
- Add `resource-efficiency` when cost, token use, command count, or verification churn should contribute to comparisons.
- Treat `plan-to-code`, `bugfix`, `refactor`, and `test-generation` as catalog proposals unless their definitions are active executable scorers.
- Do not author top-level `metrics`, top-level `score_profile`, or `acceptance.llm_judge_rubric`.
- `llm-as-judge` judge roles are scorer-owned. Scenarios do not define judge files.

## Done when

- `scorers[]` is explicit and uses active executable scorer definitions.
- Scorer-derived metric support is configured.
- Comparable-contract boundaries are documented.
