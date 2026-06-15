---
type: Page
title: Runs page (list / sidebar)
description: The run browser — a filterable sidebar grouped by experiment, with an empty-state that routes browsers toward the comparison view.
resource: ../../../review-surface/src/pages/RunsPage.tsx
tags: [page, runs, navigation]
timestamp: 2026-06-15T00:00:00Z
route: "/runs"
---

# Runs page (list / sidebar)

Route `/runs` (and `/runs/:runId`). A left sidebar lists every projected run;
the main pane shows either the [run detail](./run-detail.md) (when a run is
selected) or an empty state.

## Regions, questions, and data

| Region | Question it answers | Component | Data |
|---|---|---|---|
| Filter input | How do I find a specific run? | inline `<input>` | filters on `scenario`, `agent_spec`, `id` |
| Grouped run list | What runs exist, grouped by experiment? | [`RunListItem`](../components/run-list-item.md) | grouped by `experiment_id`; header shows `scenario@revision` + `agent_spec` |
| Run list item | How did this run score and what's wrong with it? | [`RunListItem`](../components/run-list-item.md) + [`ScoreVerdict`](../components/verdict.md) + [`FindingChips`](../components/finding-chips.md) | `composite_score`, `unscored`, `status`, `finding_counts`, `synthetic` |
| No-match message | Did my filter match anything? | inline | filtered list length |
| Empty main pane | Where should I start if I have no run in mind? | inline | links to [Experiments](./experiments.md) |

## Behaviours (regression-covered)

- Filtering narrows the list and shows "No runs match the filter" when empty.
- Selecting a run navigates to `/runs/:runId` (sidebar stays mounted).
- The empty state's "Experiments" link returns to `/`.

## Notes

- Runs are grouped by `experiment_id`; the group header is sticky while scrolling.
- Data source: [`/api/runs`](../data/api.md) → `data/runs.json`.
