---
type: Component
title: Badge
description: A small uppercase pill (e.g. SYNTHETIC, INVALID) flagging a run's provenance or validity.
resource: ../../../review-surface/src/components/Badge.tsx
tags: [component, indicator]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail, runs-index, experiments]
---

# Badge

**Purpose.** A minimal provenance/validity flag.

**Question answered.** *Is this run synthetic (demo fixture) or invalid?*

**Data.** A label string + optional colour, driven by `synthetic` / `valid` on a
`RunRecord` or `ExperimentRecord`.

**Interactions.** Display only.

**Page.** Run [header](./run-header.md), [run list items](./run-list-item.md),
and the Experiments comparison rows.
