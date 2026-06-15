---
type: Component
title: GateChips
description: One chip per verification gate for an at-a-glance pass/fail scan, each linking to the gate's evidence span.
resource: ../../../review-surface/src/components/GateChips.tsx
tags: [component, run-detail, gates]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail]
---

# GateChips

**Purpose.** A fast pass/fail read of the verification gates before reading any
scores.

**Question answered.** *Which gates passed and which failed?*

**Data.** `Span[]` filtered to names starting `gate:` plus their `status`
(derived from gate exit codes in the projection).

**Interactions.** Clicking a chip selects that gate's evidence span in the
[span tree](./span-tree.md) (`?span=`), the entry point for a failing-gate
investigation.

**Page.** Run detail (inside the [run header](./run-header.md)).
