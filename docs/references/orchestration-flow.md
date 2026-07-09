# Raidar Orchestration Flow

End-to-end flow for scenario execution, runtime stack validation, Harbor runtime orchestration, scoring, and experiment artifacts.

## 1. Scenario Resolution

1. Select a versioned scenario file: `scenarios/<scenario-name>/v###/scenario.yaml`.
2. Load `ScenarioDefinition` with:
   - `name`
   - `scenario_revision`
   - `environment`
   - `starter.root`
   - `prompt.entry` and optional `prompt.includes`
   - `verification`
   - `requirements.items`
   - weighted `scorers`
3. Resolve the starter from the scenario revision directory (`scenario_dir / starter.root`).
4. Copy the starter into the run workspace and inject one rules file from `scenarios/<scenario>/v###/rules/` for the selected harness.

## 2. Runtime Stack Resolution

Each run resolves an effective run contract before Harbor execution:

1. `environment.kind: stack_preset` loads the matching stack definition from `environments/**/environment.yaml`.
2. `environment.kind: custom_docker` uses the image, Dockerfile, verifier runner, and capability inventory declared by the scenario.
3. Required capabilities are merged from:
   - `scenario.environment.requirements`
   - the selected verifier runner
   - attached scorer definitions
   - the selected harness definition
4. Required capabilities are checked against the resolved image's provided capabilities.
5. The task image is built or reused using the effective run contract hash.
6. The task image capability probe validates the image contains the expected runtimes, package managers, tools, and browsers.

Capability categories are inventory only:

- `runtimes`
- `package_managers`
- `tools`
- `browsers`

Scenario behavior stays in prompts, rules, verification config, requirements, and scorer code. Scorer behavior stays in the scorer implementation and metric definitions.

## 3. Execution Layout

Each experiment writes to one execution root:

`experiments/{benchmarks|research_loops}/<timestamp>__<scenario>__<revision>__<harness>__<model>__xN/`

Inside that root:

- `workspace/baseline/`: prepared starter baseline snapshot shared by the experiment runs.
- `runs/`: canonical run artifacts (`run-01`, `run-02`, ... each with `workspace/`, `harness/`, `verifier/`, `harbor/`, `run.json`, `report.md`, and any captured evidence).
- `experiment.json`: full experiment record.
- `experiment-summary.json`: aggregate experiment output.
- `report.md`: human-readable experiment summary.

## 4. Run Lifecycle

1. CLI command (`experiment run` or `matrix`) builds `RunRequest` from the scenario plus an `AgentSpec`.
2. Runner resolves the effective run contract, prepares the workspace, applies verification setup actions, validates starter preflight commands, and builds the Harbor scenario bundle.
3. Runner captures any configured pre-run evidence after preflight succeeds.
4. Runtime image preparation builds or reuses the task image and validates image capabilities.
5. Harbor executes the harness/model pair.
6. Runner hydrates the workspace from the harness final workspace archive, captures post-run evidence, then prunes transient workspace folders (`node_modules`, `.next`, `.venv`, etc.).
7. The environment-selected verifier runner loads and normalizes verifier artifacts into score outputs.
8. Scorecard metadata persists run pointers, process metrics, starter fingerprints, evidence pointers, prune metadata, effective contract metadata, image cache metadata, and experiment-start timing.

## 4.1 Matrix Config Contract

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

## 5. Scoring Pipeline

Scenario scoring capability is defined by `scenario.yaml -> scorers[]`. Each scorer is resolved from the code-backed registry in `orchestrator/src/raidar/scorers/`, scenario config is merged into metric config, and duplicate metrics are executed once.

Scorer definitions may declare capability requirements when their implementation needs concrete tooling. Those requirements are merged into the effective run contract; they do not describe scorer behavior.

Core score outputs include:

- `functional`
- `code-quality`
- `visual-regression` (optional)
- `verification-stability`
- `test-coverage`
- `requirements-coverage`
- hard gates: `execution_validity`, `performance_gates`
- ranking metric: `resource-efficiency`

Additional scorer-backed metrics include:

- `requirements-adherence`
- `plan-adherence`
- `defect-resolution`
- `regression-protection`
- `change-containment`
- `defect-evidence-completeness`
- `behavior-preservation`
- `structural-improvement`
- `public-contract-stability`
- `planned-scope-coverage`
- `acceptance-evidence-completeness`
- `requirement-mapping`
- `assertion-strength`
- `coverage-lift`
- `production-code-guardrail`
- `artifact-checks`

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

## 6. Canonical Analysis Inputs

Use these artifact paths for human or automated review:

- `experiments/benchmarks/*/experiment.json`
- `experiments/research_loops/*/experiment.json`
- `experiments/*/experiment-summary.json`
- `experiments/*/report.md`
- `experiments/*/runs/*/run.json`
- `experiments/*/runs/*/verifier/scorecard.json`
- `experiments/*/runs/*/verifier/execution-validity.json`
- `experiments/*/runs/*/harness/*.txt` for harness logs

Useful runtime metadata fields:

- `scores.metadata.harbor.cache.contract.id`
- `scores.metadata.harbor.cache.contract.hash`
- `scores.metadata.harbor.cache.contract.cache_payload`
- `scores.metadata.harbor.cache.provided_capabilities`
- `scores.metadata.harbor.cache.required_capabilities`
- `scores.metadata.harbor.cache.image_cache_hit`
- `scores.metadata.time_to_experiment_start_sec`

## 7. Runtime Stack Smoke

Use the runtime-stack smoke target when changing scenario environment requirements, environment presets, scorer requirements, harness definitions, or task-image validation:

```bash
make runtime-stack-scenario-smoke \
  SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml \
  HARNESS=codex-cli \
  PROVIDER=openai \
  MODEL=gpt-5.5 \
  REASONING_EFFORT=low
```

The target runs a warm-up and a measured run, then validates persisted runtime stack metadata and warm-image start behavior.

## 8. Cleanup Lifecycle

`make experiments-prune`:

- prunes older experiment roots per model via `KEEP_PER_MODEL`.
- archives pruned artifacts under `/tmp/raidar-archive/<timestamp>/` by default.
