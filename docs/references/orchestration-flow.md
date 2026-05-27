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
   - weighted `scorers`
3. Resolve the starter from the scenario revision directory (`scenario_dir / starter.root`).
4. Copy the starter into the run workspace and inject one rules file from `scenarios/<scenario>/v###/rules/` for the selected harness.

## 2. Execution Layout

Each experiment writes to one execution root:

`experiments/{benchmarks|research_loops}/<timestamp>__<scenario>__<revision>__<harness>__<model>__xN/`

Inside that root:
- `workspace/baseline/`: prepared starter baseline snapshot shared by the experiment runs.
- `runs/`: canonical run artifacts (`run-01`, `run-02`, ... each with `workspace/`, `harness/`, `verifier/`, `harbor/`, `run.json`, `report.md`, and any captured evidence).
- `experiment.json`: full experiment record.
- `experiment-summary.json`: aggregate experiment output.
- `report.md`: human-readable experiment summary.

## 3. Run Lifecycle

1. CLI command (`experiment run` or `matrix`) builds `RunRequest` from the scenario plus an `AgentSpec`.
2. Runner prepares the workspace, validates starter preflight commands, and builds the Harbor scenario bundle.
3. Runner captures any configured pre-run evidence after preflight succeeds.
4. Harbor executes the harness/model pair.
5. Runner hydrates the workspace from `final-app.tar.gz`, captures post-run evidence, then prunes transient workspace folders (`node_modules`, `.next`, etc.).
6. Verifier artifacts are loaded and normalized into score outputs.
7. Scorecard metadata persists run pointers, process metrics, starter fingerprints, evidence pointers, and prune metadata.

## 3.1 Matrix Config Contract

Use the public matrix schema:

```yaml
matrix:
  id: homepage-codex
  scenario: scenarios/homepage-implementation
  experiment:
    timeout_sec: 1800
    repeats: 3
    repeat_parallel: 1
    retry_void: 1
  entries:
    - id: codex-gpt-5-5-low-v001
      scenario_revision: v001
      agent:
        harness: codex-cli
        provider: openai
        model: gpt-5.5
        reasoning_effort: low
    - id: claude-haiku-4-5-v001
      scenario_revision: v001
      agent:
        harness: claude-code
        provider: anthropic
        model: claude-haiku-4-5
```

`AgentSpec` means `harness + model`. Matrix files live under `matrices/`, and `matrix.scenario` points at the scenario root while each entry selects a revision.

## 4. Scoring Pipeline

Scenario scoring capability is defined by `scenario.yaml -> scorers[]`. Each scorer is resolved from the code-backed registry in `orchestrator/src/raidar/scorers/`, scenario config is merged into metric config, and duplicate metrics are executed once.

Core score outputs:
- `functional`
- `acceptance`
- `code-quality`
- `visual-regression` (optional)
- `verification-stability`
- `test-coverage`
- `requirements-coverage`
- hard gates: `execution_validity`, `performance_gates`
- ranking metric: `resource-efficiency`

Canonical metric output:
- `metric_scores[]` in verifier scorecards and persisted run scorecards.
- `artifact-checks` and scorer-owned `llm-as-judge` metrics appear alongside core metrics as scalar metric scores when resolved.

Scorer output:
- `scorer_results[]` includes scorer id, version, category, scenario weight, score, and metric contributions.
- `quality_score` is computed from quality-category scorer results only.
- `composite_score` is computed from all scorer results after unscored and execution-validity gating.
- `minimum_quality_score` performance gating is recomputed from canonical scorer output after orchestrator-owned metrics have run.

Evaluation profile:
- `evaluation_profile` is derived from weighted scorers as `scorers:<scorer-id>@<version>:<weight>+...`.
- Persisted in `run.json` config and experiment config.

`composite_score` is gated: unscored or execution-invalid runs score `0.0`.

## 5. Canonical Analysis Inputs

Use these artifact paths for human or automated review:
- `experiments/benchmarks/*/experiment.json`
- `experiments/research_loops/*/experiment.json`
- `experiments/*/experiment-summary.json`
- `experiments/*/report.md`
- `experiments/*/runs/*/run.json`
- `experiments/*/runs/*/verifier/scorecard.json`
- `experiments/*/runs/*/verifier/execution-validity.json`
- `experiments/*/runs/*/harness/*.txt` for harness logs

## 6. Cleanup Lifecycle

`make experiments-prune`:
- prunes older experiment roots per model via `KEEP_PER_MODEL`.
- archives pruned artifacts under `/tmp/raidar-archive/<timestamp>/` by default.
