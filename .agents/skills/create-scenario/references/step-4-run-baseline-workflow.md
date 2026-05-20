# Step 4 Workflow: Run Baseline

## Objective

Create the first evidence point before any revision work.

## Required actions

1. Run the target AgentSpec against the baseline, or run the stored matrix with `make matrix-run CONFIG=matrices/<matrix>.yaml`.
2. For matrix runs, treat each `matrix.entries[]` item as a distinct scenario revision and AgentSpec evidence point.
3. Collect aggregate, `scores.scorer_results[]`, `scores.metric_scores[]`, quality, composite, and resource-efficiency evidence.
4. Use the results as input to the revision skill instead of pre-authoring revisions.

## Done when

- Baseline run evidence exists with scorer and metric score outputs.
- Matrix-backed baseline evidence is tied to stored matrix entry ids.
- Results are ready for revision analysis.
- No future revision was authored before analysing the baseline.
