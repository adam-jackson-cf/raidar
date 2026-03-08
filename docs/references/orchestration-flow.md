# Raidar Orchestration Flow

End-to-end flow for scenario execution, Harbor runtime orchestration, and experiment artifacts.

## 1. Scenario Resolution

1. Select a versioned scenario file: `scenarios/<scenario-name>/v###/scenario.yaml`.
2. Load `ScenarioDefinition` with:
   - `name`
   - `scenario_revision`
   - `starter.root`
   - `prompt.entry` and optional `prompt.includes`
   - `verification`
   - `acceptance`
   - ordered `metrics`
3. Resolve the starter from the scenario revision directory (`scenario_dir / starter.root`).
4. Copy the starter into the run workspace and inject one rules file from `scenarios/<scenario>/v###/rules/`.

## 2. Execution Layout

Each experiment writes to one execution root:

`experiments/<timestamp>__<scenario>__<revision>__<agent>__<model>__xN/`

Inside that root:
- `workspace/baseline/`: prepared starter baseline snapshot shared by the experiment runs.
- `runs/`: canonical run artifacts (`run-01`, `run-02`, ... each with `workspace/`, `agent/`, `verifier/`, `harbor/`, `run.json`, `report.md`, and any captured evidence).
- `experiment.json`: full experiment record.
- `experiment-summary.json`: aggregate experiment output.
- `report.md`: human-readable experiment summary.

## 3. Run Lifecycle

1. CLI command (`experiment run` or `matrix`) builds `RunRequest` from scenario + agent config.
2. Runner prepares the workspace, validates starter preflight commands, and builds the Harbor scenario bundle.
3. Runner captures any configured pre-run evidence after preflight succeeds.
4. Harbor executes the agent/model pair.
5. Runner hydrates the workspace from `final-app.tar.gz`, captures post-run evidence, then prunes transient workspace folders (`node_modules`, `.next`, etc.).
6. Verifier artifacts are loaded and normalized into score outputs.
7. Scorecard metadata persists run pointers, process metrics, starter fingerprints, evidence pointers, and prune metadata.

## 4. Scoring Pipeline

Scenario scoring capability is defined by ordered `scenario.yaml -> metrics[]`.

Core score outputs:
- `functional`
- `acceptance`
- `visual` (optional)
- `verification_stability`
- `test_coverage`
- `requirements_coverage`
- hard gates: `execution_validity`, `performance_gates`
- ranking metric: `resource_efficiency`

Metric output:
- `metric_results[]` in verifier scorecards and persisted run scorecards.
- `artifact-checks` is audit-only unless an experiment contract explicitly makes it gating.

Evaluation profile:
- `evaluation_profile` is derived from ordered metrics as `v2:<metric-id>+...`.
- Persisted in `run.json` config and experiment config.

`composite_score` is gated: unscored or execution-invalid runs score `0.0`.

## 5. Canonical Analysis Inputs

Use these artifact paths for human or automated review:
- `experiments/*/experiment.json`
- `experiments/*/experiment-summary.json`
- `experiments/*/report.md`
- `experiments/*/runs/*/run.json`
- `experiments/*/runs/*/verifier/scorecard.json`
- `experiments/*/runs/*/verifier/execution-validity.json`
- `experiments/*/runs/*/agent/*.txt`

## 6. Cleanup Lifecycle

`make experiments-prune`:
- prunes older experiment roots per model via `KEEP_PER_MODEL`.
- archives pruned artifacts under `/tmp/raidar-archive/<timestamp>/` by default.
