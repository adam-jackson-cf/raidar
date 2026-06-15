---
type: Component
title: FailurePatterns
description: Rollup of issue findings and failed gates across a scenario family to surface what recurringly breaks.
resource: ../../../review-surface/src/components/FailurePatterns.tsx
tags: [component, experiments, failure-analysis]
timestamp: 2026-06-15T00:00:00Z
appears_on: [experiments]
---

# FailurePatterns

**Purpose.** Aggregates failure signals across all runs in a family so patterns
(not one-off flukes) become visible.

**Question answered.** *What keeps breaking across runs in this scenario?*

**Data.** `RunRecord[]`: `failed_gates` and `issue_categories` summed across the
family, rendered as ranked [finding categories](../concepts/finding-categories.md).

**Interactions.** Links through to runs exhibiting the pattern.

**Page.** Experiments (per family, beside the [trade-off scatter](./tradeoff-scatter.md)).
