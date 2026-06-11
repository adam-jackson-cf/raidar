# Creating A New Scenario

Use this guide to create a versioned scenario that runs in the orchestrator for any supported `AgentSpec` (`harness + model`).

## 1. Create Versioned Scenario Structure

Create:

- `scenarios/<scenario-name>/v001/scenario.yaml`
- `scenarios/<scenario-name>/v001/prompt/task.md`
- `scenarios/<scenario-name>/v001/rules/`
- `scenarios/<scenario-name>/v001/starter/`

## 2. Author `scenario.yaml`

Current schema shape:

```yaml
name: homepage-implementation
scenario_revision: v001
description: Implement homepage matching provided reference design
difficulty: medium
category: greenfield-ui
timeout_sec: 1800

starter:
  root: starter

verification:
  max_gate_failures: 3
  required_commands:
    - ["bun", "run", "typecheck"]
  gates:
    - name: typecheck
      command: ["bun", "run", "typecheck"]
      on_failure: continue
  coverage_threshold: 0.8
  min_quality_score: 0.8

acceptance:
  deterministic_checks: []
  requirements: []

scorers:
  - id: typescript-code-task
    version: 1
    weight: 0.9
    config:
      artifact-checks:
        required_paths:
          - src/**/*.ts
          - src/**/*.tsx
        path_match: glob
  - id: resource-efficiency
    version: 1
    weight: 0.1

prompt:
  entry: prompt/task.md
  includes: []
```

Notes:

- Keep implementation instructions in prompt artifacts, not in YAML prose blocks.
- Command fields must be argv arrays. Do not use shell wrappers, operators, or `-c`.
- Rules are single-set only. Do not add strict/minimal variants.
- `scorers[]` is required and defines the scenario evaluation profile.
- Scorer refs must point to active executable definitions registered by code under `orchestrator/src/raidar/scorers/`.
- Scenario YAML is strict. Removed fields such as top-level `metrics`, top-level `score_profile`, and `acceptance.llm_judge_rubric` fail validation.
- Scenarios may declare retained evidence files the agent must write during the run:

```yaml
evidence:
  retained_files:
    - path: evidence/defect-evidence.json
      description: Reproduction note, regression test paths, and verification evidence.
```

  Declared files must be JSON objects in the run workspace. After the run, their top-level string and string-list fields are ingested into scorer-visible retained evidence (size-capped; platform keys are protected). Scorers such as `bugfix@1` consume these fields for evidence-completeness metrics. See `scenarios/bugfix-ledger-balance/v001/` for a working example.

## 2.1 Configure Scorers

Active scorer IDs:

- `design-to-code`
- `typescript-code-task`
- `requirements`
- `resource-efficiency`

Attach one or more scorers and use scorer-level `weight` to express the scenario blend. The homepage scenario uses `design-to-code` for quality and `resource-efficiency` for cost-aware comparison:

```yaml
scorers:
  - id: design-to-code
    version: 1
    weight: 0.9
    config:
      artifact-checks:
        required_paths:
          - src/app/page.tsx
          - src/components/**/*.tsx
        path_match: glob
  - id: resource-efficiency
    version: 1
    weight: 0.1
```

Dependency rules:

- Scorer weights must have a positive total.
- Metric weights inside scorer definitions must have a positive total.
- `test-coverage` requires `verification.coverage_threshold`.
- `requirements-coverage` requires non-empty `acceptance.requirements`.
- `visual-regression` requires a `visual` block.
- `artifact-checks` requires non-empty `config.required_paths`.
- `llm-as-judge` is scorer-owned. Scenarios cannot override judge role files.
- `verification.min_quality_score` requires at least one quality-category scorer. Set it to `0.0` only for efficiency-only smoke scenarios.

The profile shown in run and experiment artifacts is derived from scorer refs as:

`scorers:<scorer-id>@<version>:<weight>+...`

## 2.2 Judge Role Files

When a scorer includes `llm-as-judge`, the judge role file lives under `orchestrator/src/raidar/scorers/definitions/` and is referenced by the code-backed scorer definition:

```yaml
- id: plan-quality
  type: llm-as-judge
  weight: 0.35
  config:
    judge: judges/plan-judge.toml
```

The judge role file should contain the judge role, responsibilities, rubric, and output contract. Keep those details out of `scenario.yaml`.
The `judge` path must stay inside scorer definitions; absolute paths and `..` traversal are rejected.

## 3. Create Rules Files

Populate `scenarios/<scenario>/v001/rules/` with harness-mapped files:

- `AGENTS.md`
- `CLAUDE.md`
- `GEMINI.md`
- `copilot-instructions.md`
- `user-rules-setting.md`

## 4. Validate And Run

1. Validate the scenario:

```bash
make scenario-validate SCENARIO=scenarios/<scenario-name>/v001/scenario.yaml
```

2. Run a smoke experiment:

```bash
make experiment-run \
  SCENARIO=scenarios/<scenario-name>/v001/scenario.yaml \
  HARNESS=codex-cli \
  PROVIDER=openai \
  MODEL=gpt-5.5 \
  REASONING_EFFORT=low
```

3. When you compare multiple `AgentSpec`s, author the matrix config with `matrix.id`, `matrix.scenario`, `matrix.experiment`, and `matrix.entries`; each entry declares `scenario_revision` and a nested `agent` with `harness`, `provider`, `model`, and optional `reasoning_effort`.

## 5. Revision Pattern

When iterating scenario behavior, create `v002`, `v003`, etc., and evolve:

- prompt artifacts
- rules
- starter files
- scenario config
- scorer refs and scorer-specific config

Use deterministic revision cloning:

```bash
make scenario-clone-revision SCENARIO_DIR=scenarios/<scenario-name> FROM_REVISION=v001
```

This creates `v002` automatically and updates `scenario.yaml` revision metadata in the cloned version.

Do not mutate old revisions once they have been used for benchmark comparisons.
