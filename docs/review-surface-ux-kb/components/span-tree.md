---
type: Component
title: SpanTree
description: The hierarchical execution trace — agent steps, gates, and scoring phases — with a duration timeline, error cycling, expand/collapse, and keyboard navigation.
resource: ../../../review-surface/src/components/SpanTree.tsx
tags: [component, run-detail, trace, spans]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail]
---

# SpanTree

**Purpose.** The deepest layer — the trace walked by the
[harness debugger](../personas/harness-debugger.md). Span names map 1:1 to the
Raidar trace.

**Question answered.** *How is the run structured, where is the error, and where
did the time go?*

**Data.** `Span[]`: `id`, `parent_span_id` (hierarchy), `name`, `span_type`,
`status`, `start_time_ms` / `end_time_ms` / `duration_ms` (timeline bars), plus
span-scoped `Annotation[]` ([chips](./annotation-chip.md)). Selection via
`selectedSpanId` (`?span=`).

**Interactions.** Click a row (`data-span-row`) to select; arrow keys
(↑↓ move, ←→ fold), Esc clears; "Expand all" / "Collapse to sections" buttons;
an error-cycle button steps through `status: ERROR` spans. Exposes `role="tree"`.

**Page.** Run detail (centre, ~60% when a span is selected).
