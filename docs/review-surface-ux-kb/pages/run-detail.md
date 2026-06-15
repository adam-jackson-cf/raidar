---
type: Page
title: Run detail
description: The deep run view — verdict banner, scorecard breakdown, findings & annotations, per-run search, span tree, and span detail — stacked from plain-language verdict down to raw payload.
resource: ../../../review-surface/src/pages/RunsPage.tsx
tags: [page, run-detail, scoring, trace]
timestamp: 2026-06-15T00:00:00Z
route: "/runs/:runId"
persona: eval-engineer
---

# Run detail

Rendered by `RunDetailView` inside the [Runs page](./runs-index.md) when a
`:runId` is present. It is the spine of the [customer journey's](../journeys/customer-journey.md)
Explain and Trace stages, stacked top-to-bottom from verdict to raw evidence.

## Regions, questions, and data

| Region (top → bottom) | Question it answers | Component | Data |
|---|---|---|---|
| **Verdict banner** | In one sentence, what happened? | [`RunHeader`](../components/run-header.md) | `runSummary()` over `composite_score`, `failed_gates`, `finding_counts`, `valid`, `unscored_reasons` |
| Gate chips (in header) | Which gates passed/failed? | [`GateChips`](../components/gate-chips.md) | `gate:*` spans + status |
| Compare-specs link | How does this run compare to siblings? | inline | links to `#family-<scenario>` |
| Technical details disclosure | What are the raw ids, sub-scores, artifacts? | [`RunHeader`](../components/run-header.md) | run id, `quality_score`, `diagnostic_score`, `artifact_paths` |
| **Scorecard** | Why did it score this — which areas, which checks? | [`ScorecardPanel`](../components/scorecard-panel.md) | `scorer:*` / `metric:*` spans (weight, score, contributions) |
| **Findings & annotations** | What did Raidar flag, and what have reviewers noted? | [`AnnotationCards`](../components/annotation-cards.md) + [`FindingChips`](../components/finding-chips.md) | `annotations` (raidar findings + user notes), `finding_counts` |
| Annotate form | How do I record what I noticed? | [`AnnotationCreateForm`](../components/annotation-create-form.md) | writes a `user` annotation (optionally span-scoped) |
| **Search** | Where in this run did X happen? | [`SearchPanel`](../components/search-panel.md) | `/api/runs/:id/search` over span payloads |
| **Span tree** | How is the run structured; where's the error/time? | [`SpanTree`](../components/span-tree.md) | `spans` (hierarchy, status, timing) + span annotations |
| **Span detail** | What's the full context of the selected span? | [`SpanDetail`](../components/span-detail.md) + [`JsonView`](../components/json-view.md) | selected `Span` payloads, model, tokens + its annotations |

## The three states (all regression-covered)

| State | Banner | What's distinctive |
|---|---|---|
| **Passing** (`Strong`) | "Delivered to spec" | green scorecard checks, `good` findings |
| **Failing** (`Failing`) | "Did not deliver" | clickable failed-gate chips, issue findings with jump-to-evidence |
| **Unscored** | "This run was not scored" | dedicated "Why this run is unscored" banner from `unscored_reasons` (e.g. "harness exited before verification") — no misleading score |

## Cross-cutting interactions

- **Span selection is URL state** (`?span=<id>`), so every deep-link (gate chip,
  scorecard check, finding jump, tree click, search result, error-cycle, keyboard
  nav) is shareable and back-button friendly.
- Selecting a span splits the layout: tree on the left (~60%), detail on the right (~40%).
- Data source: [`/api/runs/detail/:id`](../data/api.md) → `data/runs/<id>.json`.
