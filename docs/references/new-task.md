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

prompt:
  entry: prompt/task.md
  includes: []
```

Notes:
- `prompt` is artifact-driven; keep implementation instructions out of YAML body.
- command fields must be argv arrays (no shell features/operators).
- rules are single-set only (no strict/minimal/none variants).

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
uv run eval-orchestrator task validate --task ../tasks/<task-name>/v001/task.yaml
```

2. Run smoke execution:
```bash
uv run eval-orchestrator run \
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
uv run eval-orchestrator task clone-version \
  --path ../tasks/<task-name> \
  --from-version v001
```

This creates `v002` automatically, updates `task.yaml` (`version`), and rewrites
`scaffold/scaffold.manifest.json` metadata (`template`, `template_version`).

Do not mutate old versions once they are used for benchmark comparisons.
