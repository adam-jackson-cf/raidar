---
type: Data Projection
title: Projections
description: The three projection files the build script writes, and the key transformations (span projection, scoring aggregation, gate history, findings→annotations, revision diffs) that produce them.
resource: ../../../review-surface/scripts/build-review-data.mjs
tags: [data, projection, transformation]
timestamp: 2026-06-15T00:00:00Z
---

# Projections

`build-review-data.mjs` writes three files under `review-surface/data/`.

## Output files

| File | Shape | Key fields |
|---|---|---|
| `data/runs.json` | `{ runs: RunRecord[] }` (index; `/api/runs` returns `.runs`) | `id`, `scenario`, `revision`, `agent_spec`, `experiment_id`, `composite_score`, `status`, `unscored`/`unscored_reasons`, `valid`, `finding_counts`, `issue_categories`, `failed_gates`, `artifact_paths` |
| `data/runs/<id>.json` | `{ run, spans, annotations }` | full [`RunDetail`](../pages/run-detail.md) — projected spans + merged annotations |
| `data/experiments.json` | `{ experiments[], revision_diffs[] }` | per-spec `aggregate`, `sample`, `rerun`, `findings`, `run_ids` + `RevisionDiff[]` |

## Key transformations

| Transformation | From → To |
|---|---|
| **Span projection** | `run.traces[]` events → `Span[]`. `assistant_message`→`LLM_GENERATION`, `bash_command`/`tool_call`→`TOOL_CALL`, else `INTERNAL`; durations from consecutive timestamps; gate-result events patch the preceding command span's status/output. |
| **Gate history** | `run.gate_history[]` → a "verification gates" parent span + one `gate:<name>` child per gate (status from `exit_code`, payload carries `failure_category`, `is_repeat`, `stdout`, `stderr`). |
| **Scoring** | `run.scores` → a "scoring" section: `scorer:<id>@<v>` spans and `metric:<id>` spans, parented via `metric_contributions`. |
| **Evidence sections** | `run.scores` → parallel spans: requirements, execution validity (+ performance gates), process metrics, artifacts & evidence (+ workspace diff). |
| **Findings → annotations** | `findings.json` → `Annotation[]` with pre-computed `span_id` (e.g. `failed-gate`→`gate:<ref>`, `judge-review`/`deterministic-cap`→metric span, category→section map). `source: raidar`, immutable. |
| **Run status** | `unscored` OR any failed validity check OR `functional.passed === false` → `ERROR`; else `OK`. |
| **Experiment aggregation** | per-run scores → `metric_outcomes` (pass/fail counts, `pass_rate`, `mean_score`), `scorer_outcomes`, and stat blocks (mean/median/stddev/min/max) for composite, quality, duration, tokens. |
| **Revision diffing** | two revisions' scenario.yaml + task.md → LCS line diffs + change classification; `comparable_warnings` flag breaking contract/prompt changes (see [revision movement](../components/revision-movement.md)). |
| **Sample adequacy** | preserved as-is from `experiment-summary.json` (`minimum_met`, `preferred_met`, `sample_adequacy`) — drives [confidence](../concepts/sample-confidence.md). |

The projected types are defined in
[`src/utils/types.ts`](../../../review-surface/src/utils/types.ts).
