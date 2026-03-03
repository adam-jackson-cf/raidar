# Creating a New Eval Task

Use this guide to create a versioned task that runs in the orchestrator.

## 1. Create Versioned Task Structure

Create:
- `tasks/<task-name>/v001/task.yaml`
- `tasks/<task-name>/v001/prompt/task.md`
- `tasks/<task-name>/v001/rules/`
- `tasks/<task-name>/v001/scaffold` (task-local directory)

## 2. Author `task.yaml`

Current schema:

```yaml
name: homepage-implementation
version: v001
description: Implement homepage matching provided reference design
difficulty: medium
category: greenfield-ui
timeout_sec: 1800

scaffold:
  root: scaffold

verification:
  max_gate_failures: 3
  required_commands:
    - ["bun", "run", "typecheck"]
  gates:
    - name: typecheck
      command: ["bun", "run", "typecheck"]
      on_failure: continue

compliance:
  deterministic_checks: []

metrics:
  modules:
    - type: core
      id: functional
    - type: core
      id: compliance
    - type: core
      id: efficiency
    - type: core
      id: run-validity
    - type: core
      id: optimization

prompt:
  entry: prompt/task.md
  includes: []
```

Notes:
- `prompt` is artifact-driven; keep implementation instructions out of YAML body.
- command fields must be argv arrays (no shell features/operators).
- rules are single-set only (no strict/minimal/none variants).
- `metrics.modules[]` is required and defines the evaluation profile for the task.

### 2.1 Configure and Assign Metrics

Metrics are assigned to a task by declaring ordered modules in `task.yaml` under `metrics.modules`.

Core module IDs:
- `functional`
- `compliance`
- `efficiency`
- `run-validity`
- `optimization`
- `coverage-threshold`
- `requirements`
- `llm-judge`
- `visual-odiff`

Non-UI module example (`artifact_presence`):

```yaml
metrics:
  modules:
    - type: core
      id: functional
    - type: core
      id: compliance
    - type: core
      id: efficiency
    - type: core
      id: run-validity
    - type: core
      id: optimization
    - type: artifact_presence
      id: artifact_presence
      config:
        required_paths:
          - src/app/page.tsx
          - src/components/**/*.tsx
        path_match: glob
```

Module dependency rules:
- IDs must be unique.
- `coverage-threshold` requires `verification.coverage_threshold`.
- `requirements` requires non-empty `compliance.requirements`.
- `llm-judge` requires non-empty `compliance.llm_judge_rubric`.
- `visual-odiff` requires a `visual` block.
- `artifact_presence` requires non-empty `config.required_paths`.

The profile shown in run/suite artifacts and `evals list` is derived from module order as:
- `v2:<module-id>+<module-id>+...`

## 3. Create Rules Files

Populate `tasks/<task>/v001/rules/` with agent-mapped files:
- `AGENTS.md`
- `CLAUDE.md`
- `GEMINI.md`
- `copilot-instructions.md`
- `user-rules-setting.md`

## 4. Validate and Dry Run

1. Validate task:
```bash
cd orchestrator
uv run raidar task validate --task ../tasks/<task-name>/v001/task.yaml
```

2. Run smoke execution:
```bash
uv run raidar run \
  --task ../tasks/<task-name>/v001/task.yaml \
  --agent codex-cli \
  --model codex/gpt-5.2-high
```

## 5. Versioning Pattern

When iterating task behavior, create `v002`, `v003`, etc., and evolve:
- prompt artifacts
- rules
- scaffold
- task config

Use deterministic cloning for version promotion:

```bash
cd orchestrator
uv run raidar task clone-version \
  --path ../tasks/<task-name> \
  --from-version v001
```

This creates `v002` automatically and updates `task.yaml` version metadata in the cloned version.

Do not mutate old versions once they are used for benchmark comparisons.
