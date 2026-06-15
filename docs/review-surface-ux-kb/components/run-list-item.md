---
type: Component
title: RunListItem
description: A sidebar run entry — concise label, score verdict, and finding chips — for picking a run to open.
resource: ../../../review-surface/src/components/RunListItem.tsx
tags: [component, runs, navigation]
timestamp: 2026-06-15T00:00:00Z
appears_on: [runs-index]
---

# RunListItem

**Purpose.** The selectable unit of the [Runs sidebar](../pages/runs-index.md).

**Question answered.** *Which run am I looking at, how did it score, and does it
have issues?*

**Data.** `RunRecord`: `id` → `runLabel`, `composite_score`/`unscored` →
[`ScoreVerdict`](./verdict.md), `status`, `finding_counts` →
[`FindingChips`](./finding-chips.md), `synthetic` → [`Badge`](./badge.md).
Carries a `data-run-id` attribute (used by regression tests and selection).

**Interactions.** Click selects the run (`/runs/:id`); hover reveals the full id
and tier explanation.

**Page.** Runs list (left sidebar).
