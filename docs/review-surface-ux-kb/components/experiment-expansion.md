---
type: Component
title: Experiment expansion
description: The expanded body of a comparison row — run pills, "where points were lost" vs "what held up" metric bars, score-area chips, and experiment-level findings.
resource: ../../../review-surface/src/pages/ExperimentsPage.tsx
tags: [component, experiments, drilldown]
timestamp: 2026-06-15T00:00:00Z
appears_on: [experiments]
---

# Experiment expansion

**Purpose.** Splits a spec's aggregate into a diagnostic so the reviewer sees
*exactly* where the score came from before drilling into a run.

**Question answered.** *For this spec, where were points lost, what held up,
and which runs back that up?*

**Data.** From the `ExperimentRecord`:
- `metric_outcomes` — split into **failing** (`pass_rate < 1`, sorted worst-first)
  vs **passing**, each row a [`MetricOutcomeRow`](./verdict.md) with a pass-ratio
  count and score bar.
- `scorer_outcomes` — score-area chips with mean-score bars.
- `findings` — experiment-level [findings](../concepts/finding-categories.md),
  evidence-linked.
- `run_ids` — rendered as [run pills](./run-pill.md).

**Interactions.** Run pills link to run detail; findings show evidence refs.
Toggled open/closed by clicking the table row.

**Page.** Experiments (inside each comparison row).
