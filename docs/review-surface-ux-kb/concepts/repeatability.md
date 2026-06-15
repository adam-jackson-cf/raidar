---
type: Concept
title: Repeatability
description: The spread-of-results verdict — Consistent vs Volatile — derived from composite-score stddev against Raidar's repeat-variance threshold.
resource: ../../../review-surface/src/utils/verdict.ts
tags: [concept, verdict, variance]
timestamp: 2026-06-15T00:00:00Z
---

# Repeatability

`spreadTier(statBlock)` turns a composite-score `stddev` into a verdict.

| Tier | Condition | Meaning |
|---|---|---|
| **Consistent** | stddev < 0.10 | Repeats stay within ±0.1 — no variance flag |
| **Volatile** | stddev ≥ 0.10 | Repeats disagree enough for Raidar to flag variance — inspect the outliers |

The 0.10 boundary is **not arbitrary**: it is Raidar's
`REPEAT_VARIANCE_STDDEV_THRESHOLD`. At or above it Raidar raises a
[`repeat-variance`](./finding-categories.md) finding, so "Volatile" on the
surface means precisely "Raidar would flag this." Surfaced in the Repeatability
column of the [Experiments table](../pages/experiments.md).
