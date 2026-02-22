# Raidar Orchestration Flow

End-to-end flow for task execution, Harbor runtime orchestration, and scoring output.

## 1. Task Definition Resolution

1. Select a versioned task file: `tasks/<task-name>/v###/task.yaml`.
2. Load task schema (`TaskDefinition`) with:
   - `name`
   - `version`
   - `scaffold.root` (task-local scaffold path)
   - `prompt.entry` + optional `prompt.includes`
3. Resolve scaffold from task version directory (`task_dir / scaffold.root`).
4. Copy scaffold into per-repeat workspace and inject a single ruleset from `tasks/<task>/v###/rules/`.

## 2. Execution Layout (Single Top-Level Root)

Every suite/run writes to one execution root:

`executions/<timestamp>__<task>__<version>/`

Inside that root:
- `workspace/baseline/`: prepared scaffold baseline snapshot used by all runs in the suite.
- `runs/`: canonical run artifacts (`run-01`, `run-02`, ... each with `workspace/`, `agent/`, `verifier/`, `harbor/`, `run.json`, `summary.md`, `homepage-pre.png`, `homepage-post.png`).
- `suite.json`: full suite record (source of truth).
- `suite-summary.json`: suite aggregate output.
- `analysis.md`: suite-level human summary.

## 3. Run Lifecycle

1. CLI command (`run` or `suite run`) builds `RunRequest` from task + harness config.
2. Runner prepares workspace, validates scaffold preflight commands, and builds Harbor task bundle.
3. Runner captures `homepage-pre.png` after preflight passes and before Harbor task execution.
4. Harbor executes the adapter/model pair.
5. Runner hydrates workspace from `final-app.tar.gz`, captures `homepage-post.png`, then prunes transient workspace folders (`node_modules`, `.next`, etc.).
6. Verifier artifacts are loaded and normalized into score outputs.
7. Scorecard metadata persists run pointers, process metrics, scaffold fingerprints, evidence pointers, and prune metadata.

## 4. Scoring Pipeline

Dimensions:
- `functional`
- `compliance`
- `visual` (optional)
- `efficiency`
- `coverage`
- `requirements`
- hard gates: `run_validity`, `performance_gates`
- ranking metric: `optimization`

`composite_score` is gated (voided/invalid runs score `0.0`).

## 5. Reporting and Analysis Inputs

Canonical artifact paths for analysis:
- `executions/*/suite.json`
- `executions/*/suite-summary.json`
- `executions/*/runs/*/run.json`
- `executions/*/runs/*/verifier/scorecard.json`
- `executions/*/runs/*/agent/*.txt`

## 6. Cleanup Lifecycle

`uv run --project orchestrator raidar executions prune`:
- archives legacy split roots (`orchestrator/results`, `orchestrator/jobs`, `orchestrator/workspace*`) by default.
- prunes execution roots per model via `--keep-per-model` (default `1`).
- archives pruned artifacts under `/tmp/raidar-archive/<timestamp>/` by default.
