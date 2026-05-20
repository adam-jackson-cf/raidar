# Step 4 Workflow: Run And Analyse Revision

## Objective

Measure whether the revision improved under the stable contract.

## Required actions

1. Validate the revision.
2. Run the same target AgentSpec, or run the stored matrix with `make matrix-run CONFIG=matrices/<matrix>.yaml` when prior evidence was matrix-backed.
3. For matrix comparisons, compare matching AgentSpecs across prior and candidate `scenario_revision` entries.
4. Compare aggregate, `scores.scorer_results[]`, `scores.metric_scores[]`, quality, composite, resource-efficiency, validity, and gate stability to the previous accepted revision.

## Done when

- Revision run evidence exists with scorer and metric score outputs.
- Comparison uses the same scoring contract.
- Matrix-backed evidence reports the matched entry ids, scenario revisions, and AgentSpecs used for comparison.
- Result is classified without overstating secondary gains.
