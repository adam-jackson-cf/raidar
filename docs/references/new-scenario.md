# Creating a New Scenario

Use this guide to create a versioned scenario that runs in the orchestrator.

## 1. Create Versioned Scenario Structure

Create:
- `scenarios/<scenario-name>/v001/scenario.yaml`
- `scenarios/<scenario-name>/v001/prompt/task.md`
- `scenarios/<scenario-name>/v001/rules/`
- `scenarios/<scenario-name>/v001/starter/`

## 2. Author `scenario.yaml`

Current schema:

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
  llm_judge_rubric: []

metrics:
  - type: core
    id: functional
  - type: core
    id: acceptance
  - type: core
    id: verification-stability
  - type: core
    id: execution-validity
  - type: core
    id: resource-efficiency

prompt:
  entry: prompt/task.md
  includes: []
```

Notes:
- Keep implementation instructions in prompt artifacts, not in YAML prose blocks.
- Command fields must be argv arrays. Do not use shell wrappers, operators, or `-c`.
- Rules are single-set only. Do not add strict/minimal variants.
- `metrics[]` is required and defines the evaluation profile for the scenario.

## 2.1 Configure Metrics

Core metric IDs:
- `functional`
- `acceptance`
- `verification-stability`
- `execution-validity`
- `resource-efficiency`
- `test-coverage`
- `requirements-coverage`
- `llm-judge`
- `visual-regression`

Non-core metric example (`artifact-checks`):

```yaml
metrics:
  - type: core
    id: functional
  - type: core
    id: acceptance
  - type: core
    id: verification-stability
  - type: core
    id: execution-validity
  - type: core
    id: resource-efficiency
  - type: artifact-checks
    id: artifact-checks
    config:
      required_paths:
        - src/app/page.tsx
        - src/components/**/*.tsx
      path_match: glob
```

Metric dependency rules:
- IDs must be unique.
- `test-coverage` requires `verification.coverage_threshold`.
- `requirements-coverage` requires non-empty `acceptance.requirements`.
- `llm-judge` requires non-empty `acceptance.llm_judge_rubric`.
- `visual-regression` requires a `visual` block.
- `artifact-checks` requires non-empty `config.required_paths`.

The profile shown in run and experiment artifacts is derived from metric order as:
- `v2:<metric-id>+<metric-id>+...`

## 3. Create Rules Files

Populate `scenarios/<scenario>/v001/rules/` with agent-mapped files:
- `AGENTS.md`
- `CLAUDE.md`
- `GEMINI.md`
- `copilot-instructions.md`
- `user-rules-setting.md`

## 4. Validate and Run

1. Validate the scenario:

```bash
make scenario-validate SCENARIO=scenarios/<scenario-name>/v001/scenario.yaml
```

2. Run a smoke experiment:

```bash
make experiment-run \
  SCENARIO=scenarios/<scenario-name>/v001/scenario.yaml \
  AGENT=codex-cli \
  MODEL=codex/gpt-5.4-high
```

## 5. Revision Pattern

When iterating scenario behavior, create `v002`, `v003`, etc., and evolve:
- prompt artifacts
- rules
- starter files
- scenario config

Use deterministic revision cloning:

```bash
uv run --project orchestrator raidar scenario clone-revision \
  --path scenarios/<scenario-name> \
  --from-revision v001
```

This creates `v002` automatically and updates `scenario.yaml` revision metadata in the cloned version.

Do not mutate old revisions once they have been used for benchmark comparisons.
