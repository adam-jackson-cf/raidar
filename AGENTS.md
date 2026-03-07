**NEVER** surface or search files in `docs/references`, may only be access with explicit user consent (ask). If a user references a file within this exclusion treat that as automatic consent.

## Workflows

### Supported command surface

- Public interface: repo-root `make ...`
- Treat direct `uv run --project orchestrator raidar ...` as implementation detail behind the root `Makefile`.

### Internal or legacy command surfaces

- `uv run --project orchestrator raidar ...`
- `scripts/run-provider-smoke.sh`
- `scripts/run-codex-baselines.sh`

### Public Make targets to prefer in docs

- `make env-setup`
- `make provider-list`
- `make provider-validate AGENT=... MODEL=...`
- `make task-init TASK_DIR=... [TASK_VERSION=...]`
- `make task-info TASK_DIR=...`
- `make task-validate TASK=...`
- `make suite-run TASK=... AGENT=... MODEL=...`
- `make matrix-run TASK=... [CONFIG=matrix.yaml]`
- `make evals-list [METRIC_PROFILE=...] [LIMIT=...]`
- `make evals-prune [KEEP_PER_MODEL=1]`
- `make quality`

### Review workflow

- Treat `evals/.../run.json`, `evals/.../suite-summary.json`, and `evals/.../analysis.md` as the canonical review artifacts.
- Use [docs/analyze-results.md](/Users/adamjackson/Projects/typescript-ui-eval/docs/analyze-results.md) as the reference analysis guide for human review and for any future in-repo dashboard implementation.
- Matrix configs must define the top-level `suite` block with `timeout_sec`, `repeats`, `repeat_parallel`, and `retry_void`.

### Task completion

- Requires `make quality` to pass.

## Rules

- **ALWAYS** define `verification.gates[].command` as an argv list in task YAML.
- **ALWAYS** define `visual.screenshot_command` as an argv list in task YAML.
- **NEVER** use shell operators or shell features in task YAML commands.
