---
type: Component
title: ScorecardPanel
description: The "why it scored this" breakdown — each scorer area's share of the composite with bars, and each metric check click-to-evidence.
resource: ../../../review-surface/src/components/ScorecardPanel.tsx
tags: [component, run-detail, scoring]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail]
---

# ScorecardPanel

**Purpose.** The heart of the Explain stage — decomposes the composite into
scorer areas and individual metric checks, each traceable to evidence.

**Question answered.** *Why did this run score what it did — which areas
contributed, and which checks passed or failed?*

**Data.** `Span[]` filtered to `scorer:*` and `metric:*`; from the parsed
payloads: `scorer_id`, `weight`, `score`, and `metric_contributions` (each
metric's share of the composite). Pass/fail from `metric.passed`.

**Interactions.** Each check is a button (title "…of this area"); clicking
selects its evidence [span](./span-tree.md). An expander shows/hides the
breakdown. Renders behind the "Why it scored this" heading that anchors the
run-detail view.

**Page.** Run detail (middle).
