---
type: Component
title: RunHeader
description: The run-detail verdict banner — plain-language outcome, gate chips, compare-specs link, and a Technical-details disclosure for raw ids, sub-scores, and artifact paths.
resource: ../../../review-surface/src/components/RunHeader.tsx
tags: [component, run-detail, verdict]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail]
---

# RunHeader

**Purpose.** Leads the run with a verdict, not a number — the [eval engineer's](../personas/eval-engineer.md)
first read.

**Question answered.** *In one sentence, what is this run's outcome — and, on
demand, what are the raw ids and artifacts behind it?*

**Data.** `RunRecord`: `runSummary()` over `composite_score`, `failed_gates`,
`finding_counts.issue`, `valid`, `unscored` / `unscored_reasons`; the disclosure
adds `quality_score`, `diagnostic_score`, run id, `status`, and `artifact_paths`
(with copy). `synthetic` → [`Badge`](./badge.md).

**Interactions.** Tier pill → detail tooltip; "Compare agent specs" → the
originating `#family-` anchor on [Experiments](../pages/experiments.md);
"Technical details" toggles the raw block; copy buttons for artifact paths.
Hosts [`GateChips`](./gate-chips.md) as children.

**Page.** Run detail (top).
