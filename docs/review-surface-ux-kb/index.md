---
type: Knowledge Bundle
title: Raidar Review Surface — UX Knowledgebase
description: A deep-dive UX review of the Raidar Review Surface — the personas it serves, the journey they travel, and an audit of every page, component, the questions each answers, and the data behind it.
resource: ../../review-surface/README.md
tags: [ux, review-surface, raidar, okf, knowledgebase]
timestamp: 2026-06-15T00:00:00Z
okf_version: "0.1"
---

# Raidar Review Surface — UX Knowledgebase

This bundle is an [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing/)
projection of the **Raidar Review Surface**: a Workshop-inspired web app for
reviewing Raidar benchmark evidence. Each concept below is one markdown file
with YAML frontmatter; cross-links form the knowledge graph. Open
[`visualiser.html`](./visualiser.html) for the rendered, navigable view.

## What the surface is

The Review Surface answers one question chain for one audience: **"Which
agent spec delivers this scenario better → why did this run score what it
scored → where in the process did it go wrong?"** It is a *regenerable
projection* of `experiments/benchmarks/**`; the only data it owns is reviewer
annotations.

Three routes, two pages, one consistent verdict vocabulary:

- **Experiments** (`/`) — compare agent specs per scenario revision.
- **Runs** (`/runs`, `/runs/:runId`) — a run list plus a deep run-detail view.

Tagline in the app header: *Compare agent specs · Explain scores · Trace failures.*

## Map of this bundle

| Section | What it covers |
|---|---|
| [Personas](./personas/index.md) | Who uses the surface, their role, and the activities they perform |
| [Customer journey](./journeys/index.md) | The compare → explain → trace path across the three personas |
| [Pages](./pages/index.md) | The two pages / three page-views, the questions each answers, the data behind them |
| [Components](./components/index.md) | Every UI component, its question, its data, its interactions |
| [Data lineage](./data/index.md) | Source artifacts → projections → API → owned data |
| [Concepts](./concepts/index.md) | The semantic verdict vocabulary and finding-category taxonomy |

## How this audit was built

The page and component inventory was cross-checked against the Playwright
end-to-end suite ([`tests/surface.spec.ts`](../../review-surface/tests/surface.spec.ts)),
which exercises navigation, experiment comparison, all three run-detail states
(failing / unscored / passing), per-run search, the span tree, annotations,
the runs sidebar, and the comparison visualisations. Every interactive element
documented here is covered by at least one regression test, so the audit
reflects the surface as it actually behaves, not just as designed.

## Regenerating the visualiser

The bundle is the source of truth; the HTML is a generated consumer. After
editing any `.md` file, rebuild the visualiser with a zero-dependency script:

```bash
node docs/review-surface-ux-kb/build-visualiser.mjs
```

It walks the bundle, parses each file's frontmatter + body, resolves
cross-links into a graph, and writes a self-contained `visualiser.html` you can
open directly (`file://`) — no server or network required.

## First principles the design enforces

1. **Verdict first, metrics behind disclosure.** Every surface leads with a
   plain-language verdict; granular numbers sit behind progressive disclosure.
   See [verdict semantics](./concepts/verdict-semantics.md).
2. **Raidar artifacts are authoritative.** Everything shown is regenerable from
   `experiments/benchmarks/**`. See [source artifacts](./data/source-artifacts.md).
3. **Evidence is one click away.** Findings, scorecard checks, and gates all
   deep-link to the span that proves them.
