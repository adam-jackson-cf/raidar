---
type: Page Index
title: Pages
description: The two pages / three page-views of the Review Surface, the questions each answers, and the data behind them.
tags: [pages, ux, audit]
timestamp: 2026-06-15T00:00:00Z
---

# Pages

The surface has **two React pages over three routes**, wrapped in a shared
[layout](../components/layout-shell.md) (header + Experiments/Runs nav).

| Page | Route(s) | Primary persona | Core question |
|---|---|---|---|
| [Experiments](./experiments.md) | `/` | [Benchmark reviewer](../personas/benchmark-reviewer.md) | Which agent spec wins this scenario revision? |
| [Runs (list)](./runs-index.md) | `/runs` | any | Which run do I want to open? |
| [Run detail](./run-detail.md) | `/runs/:runId` | [Eval engineer](../personas/eval-engineer.md) + [harness debugger](../personas/harness-debugger.md) | Why did this run score what it scored, and where did it break? |

Routing: any unmatched path redirects to `/` (`router.tsx`). The Runs route
renders the list always and the detail view when a `:runId` is present, so the
sidebar stays visible alongside the open run.

Each page doc below lists, per UI region: the **question it answers**, the
**component(s)** that render it, and the **data** that feeds it.
