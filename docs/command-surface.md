# Command Surface

This repo should present one supported public command surface:

```bash
make ...
```

Everything else is implementation detail behind that surface.

## Public Surface

Use the repo-root `Makefile` for common workflows.

### Orchestration and validation

```bash
make env-setup
make provider-list
make provider-validate AGENT=codex-cli MODEL=codex/gpt-5.2-high
make task-info TASK_DIR=tasks/homepage-implementation/v001
make task-validate TASK=tasks/homepage-implementation/v001/task.yaml
make quality
```

### Eval execution

```bash
make suite-run TASK=... AGENT=... MODEL=...
make matrix-run TASK=... CONFIG=matrix.yaml
make evals-list [METRIC_PROFILE=...] [LIMIT=...]
make evals-prune [KEEP_PER_MODEL=1]
```

## Internal Surfaces

These should not be treated as primary user-facing commands in docs:

- `uv run --project orchestrator raidar ...`
- `scripts/run-provider-smoke.sh`
- `scripts/run-codex-baselines.sh`

They exist to implement or shortcut the public `make` surface above.

## Standard Workflow Model

From the repo root:

1. Validate environment and provider:

```bash
make env-setup
make provider-validate AGENT=codex-cli MODEL=codex/gpt-5.2-high
```

2. Run evals:

```bash
make suite-run TASK=... AGENT=... MODEL=...
make matrix-run TASK=... CONFIG=matrix.yaml
```

3. Review local artifacts:

```bash
make evals-list
```

Then analyze the generated suite artifacts using [docs/analyze-results.md](/Users/adamjackson/Projects/typescript-ui-eval/docs/analyze-results.md).
