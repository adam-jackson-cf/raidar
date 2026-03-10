**NEVER** surface or search files in `docs/references`, may only be access with explicit user consent (ask). If a user references a file within this exclusion treat that as automatic consent.

## Workflows

### Supported command surface

- Public interface: repo-root `make ...`
- Treat direct `uv run --project orchestrator raidar ...` as implementation detail behind the root `Makefile`.

### Internal or legacy command surfaces

- `uv run --project orchestrator raidar ...`
- `scripts/run-agent-smoke.sh`
- `scripts/run-codex-baselines.sh`

### Public Make targets to prefer in docs

- `make env-setup`
- `make agent-list`
- `make agent-validate AGENT=... MODEL=...`
- `make scenario-init SCENARIO_DIR=... [SCENARIO_REVISION=...]`
- `make scenario-info SCENARIO_DIR=...`
- `make scenario-validate SCENARIO=...`
- `make experiment-run SCENARIO=... AGENT=... MODEL=...`
- `make matrix-run SCENARIO=... [CONFIG=matrix.yaml]`
- `make experiments-list [EVALUATION_PROFILE=...] [LIMIT=...]`
- `make experiments-prune [KEEP_PER_MODEL=1]`
- `make quality`

### Review workflow

- Treat `experiments/.../runs/*/run.json`, `experiments/.../experiment-summary.json`, and `experiments/.../report.md` as the canonical review artifacts.
- Use [docs/analyze-results.md](/Users/adamjackson/Projects/raidar/docs/analyze-results.md) as the reference analysis guide for human review and for any future in-repo dashboard implementation.
- Matrix configs must define the top-level `suite` block with `timeout_sec`, `repeats`, `repeat_parallel`, and `retry_void`.

### Task completion

- Requires `make quality` to pass.

## Rules

- **ALWAYS** define `verification.gates[].command` as an argv list in scenario YAML.
- **ALWAYS** define `visual.screenshot_command` as an argv list in scenario YAML.
- **NEVER** use shell operators or shell features in scenario YAML commands.
