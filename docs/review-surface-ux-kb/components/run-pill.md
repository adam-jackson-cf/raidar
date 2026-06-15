---
type: Component
title: RunPill
description: A compact per-run chip inside an expanded comparison row — score-tier dot, run label, composite score, and finding chips, linking to the run.
resource: ../../../review-surface/src/pages/ExperimentsPage.tsx
tags: [component, runs, experiments]
timestamp: 2026-06-15T00:00:00Z
appears_on: [experiments]
---

# RunPill

**Purpose.** Surfaces the individual runs behind a spec aggregate so the
reviewer can jump from "this spec lost points" to the specific run that did it.

**Question answered.** *How did this individual run do, and is it worth opening?*

**Data.** `RunRecord`: `composite_score` → [tier](../concepts/delivery-tiers.md)
dot+number, `status` (ERROR styles red), `unscored`, and
[`FindingChips`](./finding-chips.md) from `finding_counts`. Label via `runLabel(id)`.

**Interactions.** Links to `/runs/:id`. Tooltip carries the full id, tier, and
composite.

**Page.** Experiments (inside [experiment expansion](./experiment-expansion.md)).
