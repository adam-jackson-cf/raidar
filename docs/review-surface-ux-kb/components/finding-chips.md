---
type: Component
title: FindingChips
description: Compact issue/good/note count chips summarising a run's findings.
resource: ../../../review-surface/src/components/FindingChips.tsx
tags: [component, findings, indicator]
timestamp: 2026-06-15T00:00:00Z
appears_on: [run-detail, experiments, runs-index]
---

# FindingChips

**Purpose.** A three-count summary of a run's findings, reused wherever a run is
represented.

**Question answered.** *How many issues, good marks, and notes does this run
have?*

**Data.** `finding_counts` (`{ issue, good, note }`) — on a `RunRecord`, a
[run pill](./run-pill.md), or the run-detail findings header.

**Interactions.** Display only.

**Page.** Run detail, Experiments (run pills / table), Runs list.
