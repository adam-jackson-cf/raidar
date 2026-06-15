---
type: Component
title: SearchPanel
description: Full-text and regex search across a run's span payloads, returning matches that select their span.
resource: ../../../review-surface/src/components/SearchPanel.tsx
tags: [component, run-detail, search, trace]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail]
---

# SearchPanel

**Purpose.** Lets the [harness debugger](../personas/harness-debugger.md) find a
command, error, or path anywhere in a large trace without manual scrolling.

**Question answered.** *Where in this run did X happen?*

**Data.** `runId` + query; results from
[`/api/runs/:id/search`](../data/api.md) as `SearchMatch[]` (`span_id`,
`span_name`, `scope` ∈ span_input/output/attributes, `snippet`, `match_range`).

**Interactions.** Type a pattern, optional **regex** checkbox, Search; the match
count renders ("N matches"); clicking a result selects its [span](./span-tree.md)
with the match highlighted.

**Page.** Run detail (above the span tree).
