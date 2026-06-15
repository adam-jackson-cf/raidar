---
type: Component Index
title: Components
description: Every UI component of the Review Surface — the question each answers, the data it consumes, and the page it lives on.
tags: [components, ux, audit]
timestamp: 2026-06-15T00:00:00Z
---

# Components

The surface is ~17 React components plus a few inline regions. They are
adapted from Raindrop Workshop's app and re-skinned over Raidar's data model.
Each file below documents one component as an OKF concept: **purpose →
question → data → interactions → page**.

## Catalog

| Component | Question it answers | Page |
|---|---|---|
| [Layout shell](./layout-shell.md) | Where am I; how do I switch views? | all |
| [Verdict (Tier / ScoreVerdict / ScoreBar)](./verdict.md) | What tier is this score? | all |
| [GroupHeadline](./group-headline.md) | Who wins this revision and what do I open first? | Experiments |
| [Experiment expansion](./experiment-expansion.md) | Where were points lost vs held? | Experiments |
| [RunPill](./run-pill.md) | How did this individual run do? | Experiments |
| [TradeoffScatter](./tradeoff-scatter.md) | Which runs are fast *and* good? | Experiments |
| [FailurePatterns](./failure-patterns.md) | What keeps breaking? | Experiments |
| [RevisionMovement](./revision-movement.md) | Did the contract change move the score? | Experiments |
| [RunListItem](./run-list-item.md) | Which run am I looking at? | Runs list |
| [RunHeader](./run-header.md) | What's the run's overall outcome? | Run detail |
| [GateChips](./gate-chips.md) | Which gates passed/failed? | Run detail |
| [ScorecardPanel](./scorecard-panel.md) | Why did it score this? | Run detail |
| [AnnotationCards](./annotation-cards.md) | What was flagged or noted? | Run detail |
| [AnnotationCreateForm](./annotation-create-form.md) | How do I record what I noticed? | Run detail |
| [AnnotationChip](./annotation-chip.md) | Is there an annotation here, of what kind? | Run detail |
| [FindingChips](./finding-chips.md) | How many issues/good/notes? | Run detail, Experiments |
| [SearchPanel](./search-panel.md) | Where in this run did X happen? | Run detail |
| [SpanTree](./span-tree.md) | How is the run structured; where's the error? | Run detail |
| [SpanDetail](./span-detail.md) | What's the full context of this span? | Run detail |
| [JsonView](./json-view.md) | What was the raw payload? | Run detail |
| [Badge](./badge.md) | Is this run synthetic / invalid? | Run detail, Runs list |

All components render through the shared [verdict vocabulary](../concepts/verdict-semantics.md)
and consume the [projected data model](../data/projections.md).
