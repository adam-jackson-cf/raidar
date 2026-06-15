---
type: Concept
title: Finding categories
description: The taxonomy of Raidar finding categories and the plain-language labels the surface gives them (raw id kept in tooltips).
resource: ../../../review-surface/src/utils/verdict.ts
tags: [concept, findings, taxonomy]
timestamp: 2026-06-15T00:00:00Z
---

# Finding categories

Raidar emits findings tagged with infra category ids. `CATEGORY_INFO` maps each
to a plain-language label + hint; the raw id stays in the tooltip. These drive
[annotation cards](../components/annotation-cards.md),
[failure patterns](../components/failure-patterns.md), and the Issues column.

| Category id | Label | Hint |
|---|---|---|
| `failed-gate` | Verification gate failed | A required check (tests, lint, …) did not pass |
| `missing-required-command` | Required step never ran | Scenario expects a command the agent never executed |
| `requirements-gap` | Requirement not satisfied | One or more scenario requirements unmet |
| `requirements-satisfied` | All requirements satisfied | Every scenario requirement met |
| `missing-artifact` | Evidence file missing | A declared evidence artifact is absent/unusable |
| `retained-evidence` | Evidence retained | Declared artifacts kept and usable |
| `deterministic-cap` | Score capped by failed checks | Deterministic prerequisites failed, capping the metric |
| `judge-review` | Judge verdict needs review | LLM-judge score disagrees with deterministic evidence |
| `completion-claim` | Claimed done without proof | Agent reported success evidence doesn't support |
| `performance-gate` | Performance threshold missed | Run breached a performance limit |
| `workflow-anomaly` | Unusual workflow | Delivery deviated from the expected pattern |
| `resource-outlier` | Unusual resource use | Tokens/duration far from scenario norm |
| `repeat-variance` | Inconsistent across repeats | Repeat runs disagree more than expected (see [repeatability](./repeatability.md)) |
| `unscored-run` | Run could not be scored | Scoring failed — needs a rerun |
| `rerun-target` | Rerun required | Experiment has unresolved unscored runs |
| `sample-adequacy` | Sample too small | Not enough scored runs to trust the aggregate (see [confidence](./sample-confidence.md)) |
| `clean-verification` | Verification clean first try | All gates passed without retries |

Each finding also carries `evidence[]` (`source`, `reference`, `detail`) that
the projection resolves to a span, enabling the "jump:" deep-links.
