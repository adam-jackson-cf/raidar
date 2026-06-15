---
type: Component
title: GroupHeadline
description: The one-sentence verdict above each comparison group — names the winning spec, its tier and score, the runner-up's gap, and the run worth opening first.
resource: ../../../review-surface/src/pages/ExperimentsPage.tsx
tags: [component, verdict, headline, experiments]
timestamp: 2026-06-15T00:00:00Z
appears_on: [experiments]
---

# GroupHeadline

**Purpose.** Translates a whole revision group into a single readable sentence
so the [reviewer](../personas/benchmark-reviewer.md) gets a verdict before any
table scanning.

**Question answered.** *Who wins this scenario revision, by how much, and which
run should I open first?*

**Data.** Sorted `ExperimentRecord[]`: best/runner-up `aggregate.composite_score.mean`
→ [tier](../concepts/delivery-tiers.md); the gap between them; and the worst run
across the group ranked by `finding_counts.issue` then score, used to build the
"Start with Run NN" link.

**Interactions.** The "Start with Run NN" run name links to that
[run detail](../pages/run-detail.md) — the primary hand-off into the journey's
Explain stage. Falls back to "No scored runs yet — rerun the benchmark" when
nothing is scored.

**Page.** Experiments, one per revision group.
