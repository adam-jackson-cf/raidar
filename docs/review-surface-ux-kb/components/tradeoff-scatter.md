---
type: Component
title: TradeoffScatter
description: A score-vs-run-time scatter, one point per run coloured by agent spec, with fast-and-good runs in the top-left.
resource: ../../../review-surface/src/components/TradeoffScatter.tsx
tags: [component, visualisation, experiments, cost-quality]
timestamp: 2026-06-15T00:00:00Z
appears_on: [experiments]
---

# TradeoffScatter

**Purpose.** Makes the quality/cost trade-off visible at a glance for a whole
scenario family.

**Question answered.** *Which runs are fast **and** good? Which are slow, or
failed outright?*

**Data.** `RunRecord[]` for the family: `composite_score` (y), `duration_ms`
(x), `status` (failed runs marked), `agent_spec` (point colour), `id`.

**Interactions.** Clicking a point opens that [run detail](../pages/run-detail.md);
hover shows run label, score, duration, id.

**Page.** Experiments (per family, beside [failure patterns](./failure-patterns.md)).
