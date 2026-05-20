# Step 0 Workflow: Load Prior Evidence

## Objective

Analyse completed benchmark evidence before authoring the next revision.

## Required actions

1. Locate the latest completed run evidence for the current revision.
2. Extract aggregate and run-level results.
3. Identify whether evidence came from a single AgentSpec run or stored matrix entries.
4. For matrix evidence, record the matrix config path, `matrix.id`, relevant entry ids, `scenario_revision`, and nested AgentSpecs.
5. Identify actual failures, unstable gates, unscored causes, and efficiency signals.

## Done when

- Prior revision evidence is available.
- Prior evidence shape is known.
- Actual improvement target is evidence-backed.
- No next revision has been authored prematurely.
