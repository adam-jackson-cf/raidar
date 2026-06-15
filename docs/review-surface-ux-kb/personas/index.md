---
type: Persona Index
title: Personas
description: The three personas the Review Surface serves, each entering at a different depth of the verdict → evidence chain.
tags: [personas, ux]
timestamp: 2026-06-15T00:00:00Z
---

# Personas

The surface is built around a **zoom-in gradient**: each persona starts
zoomed-out with a plain-language verdict and drills toward raw evidence. They
are not separate audiences with separate tools — they are the same review act
at three depths, and the UI hands a reviewer from one to the next.

| Persona | Core question | Landing surface |
|---|---|---|
| [Benchmark reviewer / platform lead](./benchmark-reviewer.md) | Which agent spec delivers this scenario better, and can I trust the sample? | [Experiments page](../pages/experiments.md) |
| [Scenario / eval engineer](./eval-engineer.md) | Why did this run score what it scored, and is the contract right? | [Run detail](../pages/run-detail.md) |
| [Agent / harness debugger](./harness-debugger.md) | Where in the delivery process did it go wrong? | [Span tree](../components/span-tree.md) |

The [customer journey](../journeys/customer-journey.md) shows how a single
investigation flows across all three.
