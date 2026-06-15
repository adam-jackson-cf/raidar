---
type: Persona
title: Benchmark reviewer / platform lead
description: Compares agent specs to decide which harness+model pairing delivers a scenario best, whether a revision helped, and whether the sample is trustworthy.
tags: [persona, comparison, decision-maker]
timestamp: 2026-06-15T00:00:00Z
persona_id: benchmark-reviewer
lands_on: experiments
---

# Benchmark reviewer / platform lead

## Role

Owns the decision of **which AgentSpec (harness + model) to back** for a given
class of delivery work. Thinks in terms of fleet-level trade-offs — delivery
quality vs. cost vs. consistency — not individual traces. Often a platform or
engineering lead who needs a defensible verdict, not a metrics dump.

## Core question

> "Which agent spec delivers this scenario better, did a revision change help,
> and can I trust the sample?"

## Activities they perform

| Activity | Where it happens | What answers it |
|---|---|---|
| Read the per-revision verdict headline | [Experiments page](../pages/experiments.md) group headline | "Best delivery: `<spec>` — delivered to spec (0.93). `<spec>` trails by 0.08." |
| Rank specs by delivery quality | [Comparison table](../pages/experiments.md) — Delivery column | [Delivery tier](../concepts/delivery-tiers.md) + Δ-vs-best |
| Judge whether results are repeatable | Repeatability column | [Repeatability / spread tier](../concepts/repeatability.md) |
| Decide whether to trust the numbers at all | Confidence column | [Sample confidence tier](../concepts/sample-confidence.md) + unscored count |
| Weigh cost against quality | Pace + Tokens columns; [trade-off scatter](../components/tradeoff-scatter.md) | duration mean, uncached input-token mean |
| See what recurringly breaks | [Failure patterns](../components/failure-patterns.md) | failed-gate + issue-category rollups |
| Decide if a revision helped or hurt | [Revision movement](../components/revision-movement.md) | score movement + contract diffs + comparability warnings |
| Pick the run worth opening first | Headline "Start with Run NN" link; [run pills](../pages/experiments.md) | most-issues / failing run |

## Where the journey hands them off

The headline's *"Start with Run NN"* link and the [run pills](../pages/experiments.md)
hand this persona to the [eval engineer](./eval-engineer.md) view — a specific
[run detail](../pages/run-detail.md). The reviewer decides *what to investigate*;
the eval engineer finds out *why*.

## Design tension to watch

The Delivery tier is a **presentation band, not a canonical Raidar grade** —
Raidar has no composite grade (see [delivery tiers](../concepts/delivery-tiers.md)).
A reviewer could over-trust "Strong" as if it were an official rating. The Δ-vs-best,
Repeatability, and Confidence columns exist precisely to keep the verdict honest.
