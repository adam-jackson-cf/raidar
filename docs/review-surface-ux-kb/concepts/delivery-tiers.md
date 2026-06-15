---
type: Concept
title: Delivery tiers
description: The composite-score verdict bands — Strong / Solid / Shaky / Failing / Unscored — shared by every score rendering.
resource: ../../../review-surface/src/utils/verdict.ts
tags: [concept, verdict, scoring]
timestamp: 2026-06-15T00:00:00Z
---

# Delivery tiers

`scoreTier(score)` maps a composite score to one band, used by every score
rendering on the surface ([Verdict](../components/verdict.md)).

| Tier | Composite | Blurb | Colour |
|---|---|---|---|
| **Strong** | ≥ 0.90 | Delivered to spec | green |
| **Solid** | ≥ 0.75 | Delivered with minor gaps | cyan |
| **Shaky** | ≥ 0.50 | Delivered with significant gaps | orange |
| **Failing** | < 0.50 | Did not deliver | red |
| **Unscored** | `null` | No score recorded — run needs a rerun | grey |

**Caveat (by design).** These are a **presentation band, not a canonical Raidar
grade** — Raidar deliberately has no composite grade. The Δ-vs-best column,
[repeatability](./repeatability.md), and [confidence](./sample-confidence.md)
exist to keep a bare tier from being over-trusted. See
[verdict semantics](./verdict-semantics.md).
