---
type: Component
title: RevisionMovement
description: Compares score / duration / token movement between scenario revisions and shows the prompt and scenario-contract diffs that may explain it — with comparability warnings.
resource: ../../../review-surface/src/components/RevisionMovement.tsx
tags: [component, experiments, revisions, diff]
timestamp: 2026-06-15T00:00:00Z
appears_on: [experiments]
---

# RevisionMovement

**Purpose.** Answers the "did our change help?" question honestly — pairing the
metric movement with the actual contract diff and flagging when the two
revisions aren't even comparable.

**Question answered.** *Did the scenario/prompt change move the score, and is
the before/after comparison valid?*

**Data.**
- `ExperimentRecord[]` per revision — `aggregate.composite_score / duration_sec /
  uncached_input_tokens` movement.
- `RevisionDiff[]` — `summary` (change classes), `comparable_warnings` (breaking
  changes), and `files.scenario` / `files.prompt` line-level diffs.

**Interactions.** Expand a card to reveal the contract diff; toggle
**scenario** / **prompt** tabs; scroll the diff block. Comparability warnings
caution against reading the movement as causal when the contract itself changed.

**Page.** Experiments (per family, below the visualisations).
