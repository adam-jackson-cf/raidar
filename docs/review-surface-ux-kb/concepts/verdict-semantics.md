---
type: Concept
title: Verdict semantics
description: The translation layer that turns raw Raidar numbers and category ids into one consistent plain-language good/bad vocabulary shared by every surface.
resource: ../../../review-surface/src/utils/verdict.ts
tags: [concept, verdict, semantics, design-principle]
timestamp: 2026-06-15T00:00:00Z
---

# Verdict semantics

**Why it exists.** Raidar's artifacts are precise but raw — a composite of
0.82, a stddev of 0.13, a category `deterministic-cap`. Reviewers need a verdict,
not arithmetic. `verdict.ts` is the single place those raw values become
words, so the Experiments table, run header, run pills, and scorecard all speak
the same language.

**The four translations.**

1. [Delivery tiers](./delivery-tiers.md) — composite → Strong / Solid / Shaky / Failing.
2. [Repeatability](./repeatability.md) — stddev vs Raidar's variance threshold → Consistent / Volatile.
3. [Sample confidence](./sample-confidence.md) — adequacy flags → Low / Fair / High.
4. [Finding categories](./finding-categories.md) — infra ids → readable labels (raw id kept in tooltips).

**Helpers in the same module.** `humanize` (kebab/snake → sentence case),
`scorerName`, `runLabel` (`…-low-04` → "Run 04"), and `runSummary` (the
one-sentence run outcome used by the [run header](../components/run-header.md)).

**The honesty rule.** Translation never invents authority. Delivery tiers are an
explicit *presentation band* — "Raidar has no canonical composite grade" — and
Repeatability is pinned to Raidar's real `REPEAT_VARIANCE_STDDEV_THRESHOLD` so
"Volatile" means exactly "Raidar would raise a variance finding." The raw values
remain one tooltip or one disclosure away everywhere.
