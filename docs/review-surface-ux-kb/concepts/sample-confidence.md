---
type: Concept
title: Sample confidence
description: The trust verdict on an aggregate — Low / Fair / High confidence — from Raidar's sample-adequacy flags.
resource: ../../../review-surface/src/utils/verdict.ts
tags: [concept, verdict, sample]
timestamp: 2026-06-15T00:00:00Z
---

# Sample confidence

`sampleTrust(sample, scored, total)` answers "can I trust this aggregate at
all?" from the scenario's sample-adequacy flags.

| Tier | Condition | Meaning |
|---|---|---|
| **Low confidence** | `minimum_met === false` | Below the minimum sample — treat results as directional only |
| **Fair confidence** | minimum met, preferred not | Minimum met, below preferred |
| **High confidence** | `preferred_met` | Preferred sample size met |

Shown in the Confidence column of the [Experiments table](../pages/experiments.md),
alongside an "N unscored" caution when runs still need a rerun. The underlying
flags are set by Raidar and [preserved unchanged](../data/projections.md) through
the projection.
