---
type: Data Source
title: Source artifacts
description: The authoritative Raidar files under experiments/benchmarks/** (and scenarios/**) that the build script reads, with the fields pulled from each.
resource: ../../../review-surface/scripts/build-review-data.mjs
tags: [data, source, artifacts]
timestamp: 2026-06-15T00:00:00Z
---

# Source artifacts

`scripts/build-review-data.mjs` reads from `experiments/benchmarks/**` and
`scenarios/**`. These are **authoritative**; the surface never mutates them.

## Per-experiment

| File | Fields used |
|---|---|
| `experiment-summary.json` | `experiment_id`, `config.{scenario_name, scenario_revision, harness, model, repeats}`, `synthetic`, `aggregate`, `sample`, `rerun`, `findings`, `created_at_utc` |

## Per-run

| File | Fields used |
|---|---|
| `runs/<run_id>/run.json` | `id`, `timestamp`, `duration_sec`, `config.model`, `traces[]`, `gate_history[]`, `scores.*` (`composite_score`, `quality_score`, `diagnostic_score`, `execution_validity.checks`, `functional.passed`, `requirements_coverage`, `resource_efficiency`, `metadata.{process, harbor, evidence}`, `scorer_results`, `metric_scores`, `unscored`, `unscored_reasons`) |
| `runs/<run_id>/findings.json` *(optional)* | `findings[]`: `id`, `kind`, `category`, `title`, `detail`, `evidence[]` |
| `runs/<run_id>/workspace-diff.json` *(optional)* | stored whole as an evidence artifact snapshot |

## Per-scenario (for revision diffing)

| File | Fields used |
|---|---|
| `scenarios/<name>/<revision>/scenario.yaml` | `description`, `difficulty`, `category`, `timeout_sec`; scorer profile, gate names, requirement ids (for change classification) |
| `scenarios/<name>/<revision>/prompt/task.md` | full text, for line-level prompt diffs |

These feed the [projections](./projections.md) and ultimately the
[verdict semantics](../concepts/verdict-semantics.md).
