---
type: Component
title: Verdict (Tier / ScoreVerdict / ScoreBar)
description: The shared verdict primitives — a tier pill, a tier+bar+number score verdict, and a bare score bar — that give the whole surface one consistent good/bad vocabulary.
resource: ../../../review-surface/src/components/Verdict.tsx
tags: [component, verdict, shared]
timestamp: 2026-06-15T00:00:00Z
appears_on: [experiments, runs-index, run-detail]
---

# Verdict (TierPill / ScoreVerdict / ScoreBar)

**Purpose.** Three exports that render the [semantic verdict layer](../concepts/verdict-semantics.md)
consistently everywhere: `TierPill` (label chip), `ScoreVerdict`
(tier + bar + numeric score), `ScoreBar` (proportional bar).

**Question answered.** *What tier is this score — Strong, Solid, Shaky,
Failing, or Unscored?*

**Data.** A `Tier` (`label`, `color`, `blurb`) from
[`scoreTier`](../concepts/delivery-tiers.md) / `spreadTier` / `sampleTrust`,
plus a `score: number | null`.

**Interactions.** Display only; the `blurb` surfaces as a hover tooltip.

**Page.** All. Used by the comparison table, run pills, run list items, the run
header, and the scorecard — making this the single source of the surface's
visual grammar.
