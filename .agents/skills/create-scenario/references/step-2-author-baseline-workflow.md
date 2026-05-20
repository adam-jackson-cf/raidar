# Step 2 Workflow: Author Baseline

## Objective

Create the scenario baseline artifacts consistently.

## Required actions

1. Create or update the scenario root using the public workflow.
2. Keep the root revision lineage as `parent_revision: null`.
3. Align prompt, rules, starter, verification, acceptance, visual config, and `scorers[]`.
4. For matrix-backed baselines, add a stored matrix under `matrices/` with `matrix.id`, `matrix.scenario`, `matrix.experiment`, and `matrix.entries[]`.
5. Prefer deterministic scorer metrics before judge-backed scoring.
6. Keep instructions conceptual, reusable, and non-scenario-specific.

## Matrix definitions

- Set `matrix.scenario` to the scenario root, not an individual revision directory.
- Give each `matrix.entries[]` item a stable `id`, a `scenario_revision`, and a nested `agent` with `harness`, `provider`, `model`, and optional `reasoning_effort`.
- Do not use legacy generated selector or config-level `agents` shapes.

## Done when

- Scenario artifacts are coherent.
- Root revision lineage is explicit.
- Matrix-backed baselines have a stored matrix definition.
- Deterministic scorer evidence is preferred where possible.
- No instruction is tailored to a previous one-off failure.
