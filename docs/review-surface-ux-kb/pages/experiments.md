---
type: Page
title: Experiments page
description: The comparison surface — per-revision verdict headline, the agent-spec comparison table, and below-the-fold trade-off, failure-pattern, and revision-movement analysis.
resource: ../../../review-surface/src/pages/ExperimentsPage.tsx
tags: [page, comparison, experiments]
timestamp: 2026-06-15T00:00:00Z
route: "/"
persona: benchmark-reviewer
---

# Experiments page

The landing page (`/`). Experiments are grouped into **scenario families →
revisions**, each revision rendered as one comparison group. A family-anchor nav
appears when more than one family is present.

## Regions, questions, and data

| Region | Question it answers | Component | Data |
|---|---|---|---|
| Family anchor nav | Which scenarios exist? | inline `<nav aria-label="Scenario families">` | distinct `scenario` keys |
| Group header | What is this scenario, how hard, what category? | inline | `scenario_meta.description / difficulty / category` |
| **Group headline** | Who wins, by how much, and what should I open first? | [`GroupHeadline`](../components/group-headline.md) | best/runner-up `composite_score.mean`; worst run by `finding_counts.issue` |
| **Comparison table** | How do the specs rank across delivery, repeatability, issues, confidence, cost? | inline table + [`Verdict`](../components/verdict.md) | per-spec `aggregate.*`, `sample`, `finding_counts` |
| — Delivery column | How well did it deliver? | [`ScoreVerdict`](../components/verdict.md) | `composite_score` (mean/median/stddev/min/max) → [tier](../concepts/delivery-tiers.md) |
| — Repeatability column | Are repeats consistent? | [`spreadTier`](../concepts/repeatability.md) | `composite_score.stddev` vs 0.1 threshold |
| — Issues column | Is anything wrong here? | inline | `findingCounts` across exp + its runs |
| — Confidence column | Can I trust this sample? | [`sampleTrust`](../concepts/sample-confidence.md) | `sample.minimum_met / preferred_met`, scored/total, `unscored_count` |
| — Pace column | How long does it take? | inline | `aggregate.duration_sec.mean` |
| — Tokens column | What does it cost? | inline | `aggregate.uncached_input_tokens.mean` |
| **Row expansion** | Where exactly were points lost vs held? | [`ExperimentExpansion`](../components/experiment-expansion.md) | `metric_outcomes`, `scorer_outcomes`, `findings`, `run_ids` |
| — Run pills | Which individual runs sit under this spec? | [`RunPill` / `FindingChips`](../components/run-pill.md) | per-run `composite_score`, `status`, `finding_counts` |
| [Trade-off scatter](../components/tradeoff-scatter.md) | Which runs are fast *and* good? | `TradeoffScatter` | per-run `composite_score`, `duration_ms`, `status`, `agent_spec` |
| [Failure patterns](../components/failure-patterns.md) | What keeps breaking in this family? | `FailurePatterns` | `failed_gates`, `issue_categories` across family runs |
| [Revision movement](../components/revision-movement.md) | Did changing the scenario move the score — and is it even comparable? | `RevisionMovement` | `aggregate` per revision + `revision_diffs` (contract/prompt diffs, comparability warnings) |

## States

- **Loading / error** — inline messages.
- **Empty** — a call-to-action with the exact commands to generate data
  (`make benchmark-fixture-synthetic && make review-surface-data`).

## Notes

- The "best" row is highlighted (accent left-border + trophy) only when the
  group has more than one spec.
- The Issues column intentionally counts **both** experiment-level findings and
  run-level findings, so a clean aggregate can't hide a troubled run.
- Data source: [`/api/experiments`](../data/api.md) → `data/experiments.json`.
