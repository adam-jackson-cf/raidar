**NEVER** surface or search files in `docs/references`, may only be access with explicit user consent (ask). If a user references a file within this exclusion treat that as automatic consent.

## Workflows

### Available scripts and purpose:

- `scripts/install-pre-commit.sh`: installs the pre-commit tooling and registers repository hooks.
- `scripts/run-provider-smoke.sh`: runs the hello-world smoke task against a selected provider/agent pair with configurable run settings.
- `scripts/run-codex-baselines.sh`: runs the homepage baseline suite across the configured Codex model set.

### Available CLI commands and purpose (`raidar`):

- `run`: execute one task against one harness/model pair.
- `suite run`: execute deterministic repeat suites with aggregate outputs.
- `quality gates`: run deterministic quality checks for orchestrator code.
- `harbor cleanup`: clean stale Harbor containers and stale Harbor build processes.
- `env setup`: bootstrap local tooling and run Harbor environment preflight.
- `evals list`: list recorded eval suites with optional filters.
- `evals prune`: archive stale eval suite artifacts with retention controls.
- `provider list`: list supported harness/provider adapters.
- `provider validate`: validate adapter wiring and runtime requirements for a harness/model pair.
- `task init`: scaffold a new versioned task package.
- `task validate`: validate a task definition.
- `task clone-version`: clone a task version to a new version label.
- `inject`: inject agent rules into a scaffold path for local testing.
- `matrix`: execute matrix runs from matrix configuration.
- `report`: build aggregate reports from eval suite outputs.
- `init_matrix`: generate an example matrix configuration template.
- `info`: display task metadata for a task package/version.

### Task completion
- Requires `uv run --project orchestrator raidar quality gates` to pass.

## Rules

- **ALWAYS** define `verification.gates[].command` as an argv list in task YAML.
- **ALWAYS** define `visual.screenshot_command` as an argv list in task YAML.
- **NEVER** use shell operators or shell features in task YAML commands.
