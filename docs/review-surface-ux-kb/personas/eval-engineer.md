---
type: Persona
title: Scenario / eval engineer
description: Explains why a single run scored what it did and validates whether the scenario contract (gates, requirements, scorers) is correct.
tags: [persona, scoring, scenario-contract]
timestamp: 2026-06-15T00:00:00Z
persona_id: eval-engineer
lands_on: run-detail
---

# Scenario / eval engineer

## Role

Owns the **scenario contract** — the requirements, verification gates, and
scorer/metric weights that decide a run's score. When a score looks wrong, this
persona must decide: did the agent genuinely underdeliver, or is the contract
mis-calibrated (a gate too strict, a requirement unstated, a judge disagreeing
with deterministic evidence)?

## Core question

> "Why did this run score what it scored, and is the scenario contract right?"

## Activities they perform

| Activity | Where it happens | What answers it |
|---|---|---|
| Read the one-sentence outcome | [Run header](../components/run-header.md) verdict banner | `runSummary()` — tier blurb + failed gates + issue count + validity |
| See the score broken into areas | [Scorecard panel](../components/scorecard-panel.md) | scorer spans → each area's share of the composite with bars |
| Trace a check to its evidence | Scorecard check buttons ("…of this area") | click selects the metric/scorer [span](../components/span-tree.md) |
| Read findings in plain language | [Findings & annotations](../components/annotation-cards.md) | Raidar `findings.json` projected as evidence-linked annotations |
| Jump from a finding to its proof | Finding "jump:" buttons | deep-link to the evidence span |
| Confirm gates passed/failed | [Gate chips](../components/gate-chips.md) | `gate:*` spans, status from exit codes |
| Understand why a run is unscored | Unscored verdict banner | `unscored_reasons` (e.g. "harness exited before verification") |
| Inspect raw ids & source artifacts | "Technical details" disclosure | run id, sub-scores, `artifact_paths` |
| Record a contract judgement | [Annotation create form](../components/annotation-create-form.md) | writes a `user` annotation |

## Where the journey hands them off

When the scorecard or a finding points at a process failure — a gate that
errored, a command that never ran — the engineer clicks through to the
[span tree](../components/span-tree.md) and becomes the
[harness debugger](./harness-debugger.md). The "Compare agent specs" link in the
run header sends them back up to the [reviewer](./benchmark-reviewer.md) view.

## Design tension to watch

Long ids and source-artifact paths are deliberately hidden behind a "Technical
details" disclosure so the scorecard stays readable — but this persona
sometimes *needs* the raw scorer id to reconcile against the scenario YAML. The
disclosure keeps that one click away rather than removing it.
