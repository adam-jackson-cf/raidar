---
type: Concept Index
title: Concepts
description: The semantic verdict vocabulary that translates raw Raidar scores, samples, and finding categories into one consistent good/bad language.
tags: [concepts, verdict, semantics]
timestamp: 2026-06-15T00:00:00Z
---

# Concepts

The surface's defining design move is a **semantic translation layer**
([`src/utils/verdict.ts`](../../../review-surface/src/utils/verdict.ts)): raw
scores, sample sizes, and finding categories become one consistent vocabulary so
every persona reads the same words for the same things.

| Concept | Translates |
|---|---|
| [Verdict semantics](./verdict-semantics.md) | The overall layer and why it exists |
| [Delivery tiers](./delivery-tiers.md) | composite score → Strong / Solid / Shaky / Failing |
| [Repeatability](./repeatability.md) | score stddev → Consistent / Volatile |
| [Sample confidence](./sample-confidence.md) | sample adequacy → Low / Fair / High confidence |
| [Finding categories](./finding-categories.md) | raw Raidar category ids → plain-language labels |
