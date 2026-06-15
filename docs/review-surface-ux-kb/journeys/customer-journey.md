---
type: Journey
title: Compare → Explain → Trace
description: The end-to-end investigation path — from "which spec wins" to "why this run scored" to "where it broke" — mapped to personas, pages, and the data behind each step.
tags: [journey, funnel, end-to-end]
timestamp: 2026-06-15T00:00:00Z
journey_id: compare-explain-trace
---

# Customer journey: Compare → Explain → Trace

One investigation, three depths. The surface's tagline —
*Compare agent specs · Explain scores · Trace failures* — **is** the journey.
Each stage zooms in one level and hands off to the next persona.

```
COMPARE                      EXPLAIN                      TRACE
(reviewer)                   (eval engineer)              (harness debugger)
Experiments page    ──▶      Run detail            ──▶    Span tree + detail
  verdict headline             verdict banner               execution timeline
  comparison table             scorecard breakdown          error cycling
  trade-off scatter            findings → evidence          payload search
  failure patterns             gate chips                   copy payload
  revision movement            technical details            annotate the span
        │                            │                            │
   "Start with Run NN" ────────▶  "jump:" / check ──────────▶  raw span
   run pill click                  click → span                evidence
```

## Stage 1 — Compare (Benchmark reviewer)

**Entry:** lands on the [Experiments page](../pages/experiments.md). Scenario
families are grouped by revision.

1. Reads the **group headline** — a single sentence naming the best spec, its
   tier and score, the runner-up's gap, and the run worth opening first.
2. Scans the **comparison table**: [Delivery](../concepts/delivery-tiers.md),
   [Repeatability](../concepts/repeatability.md),
   Issues, [Confidence](../concepts/sample-confidence.md), Pace, Tokens.
3. Expands a row to see *where points were lost* vs *what held up*, score-area
   bars, evidence-linked findings, and [run pills](../pages/experiments.md).
4. Cross-checks cost/quality on the [trade-off scatter](../components/tradeoff-scatter.md),
   recurring breakage on [failure patterns](../components/failure-patterns.md),
   and whether a revision helped on [revision movement](../components/revision-movement.md).

**Hand-off:** clicks *"Start with Run NN"* in the headline or a run pill →
[run detail](../pages/run-detail.md).

**Data behind it:** `data/experiments.json` (aggregates, metric/scorer
outcomes, sample adequacy, revision diffs) + `data/runs.json` for per-run pills.
See [projections](../data/projections.md).

## Stage 2 — Explain (Scenario / eval engineer)

**Entry:** the [run detail](../pages/run-detail.md) view opens with a
[verdict banner](../components/run-header.md).

1. Reads the **one-sentence outcome** and gate chips.
2. Works down the [scorecard](../components/scorecard-panel.md) — each score
   area shows its share of the composite; each check is click-to-evidence.
3. Reads the [findings & annotations](../components/annotation-cards.md), each
   linked to the span that proves it.
4. Opens **Technical details** only if the raw scorer ids / artifact paths are
   needed to reconcile against the scenario contract.

**Hand-off:** clicks a scorecard check or a finding's *"jump:"* button → selects
the evidence [span](../components/span-tree.md).

**Data behind it:** `data/runs/<id>.json` — the run record, projected spans, and
findings-as-annotations. See [projections](../data/projections.md).

## Stage 3 — Trace (Agent / harness debugger)

**Entry:** a span is selected in the [span tree](../components/span-tree.md);
[span detail](../components/span-detail.md) opens on the right.

1. Walks the tree (agent trace → gates → scoring), reading the duration timeline.
2. Cycles errors, or [searches](../components/search-panel.md) the run's payloads
   for a command or string.
3. Reads the full `input_payload` / `output_payload` in span detail; copies it.
4. **Annotates** the precise failure point with an
   [issue/good/note](../components/annotation-create-form.md).

**Loop closure:** the annotation and the located evidence feed back to the eval
engineer's verdict on whether the *contract* or the *agent* is at fault — and,
aggregated across runs, back to the reviewer's spec decision.

## Side paths

- **Browse-first entry:** a user can skip the comparison and enter via the
  [Runs page](../pages/runs-index.md) sidebar, filtering by scenario / spec / id.
  The empty state nudges them back to Experiments ("points you to the run worth
  opening first").
- **Unscored detour:** if the chosen run is unscored, the run detail shows a
  dedicated banner explaining *why* (e.g. harness exited before verification)
  rather than a misleading zero — see [run detail](../pages/run-detail.md).
- **Upward navigation:** "Compare agent specs" in the run header returns the
  engineer to the originating scenario family on the Experiments page.
