**NEVER** surface or search files in `docs/references`, may only be access with explicit user consent (ask). If a user references a file within this exclusion treat that as automatic consent.

## Structure

- Project index map:
- `orchestrator/` contains the Raidar runtime: evaluation orchestration, harness adapters, scoring logic, execution artifact lifecycle, and the CLI entrypoint surface.
- `tasks/` contains versioned task packages; each version groups task configuration, prompt artifacts, ruleset artifacts, and task-local scaffold content.
- `executions/` contains persisted suite outputs from completed runs, including per-run evidence and suite-level summaries.
- `scripts/` contains operator utilities for local setup, provider smoke execution, baseline execution, and release/version automation.
- `.github/` contains CI and repository automation workflows.

## Workflows

### Available scripts and purpose:

- `scripts/install-pre-commit.sh`: installs the pre-commit tooling and registers repository hooks.
- `scripts/run-provider-smoke.sh`: runs the hello-world smoke task against a selected provider/agent pair with configurable run settings.
- `scripts/run-codex-baselines.sh`: runs the homepage baseline suite across the configured Codex model set.
- `scripts/bump-version.py`: derives semantic version bumps from conventional commits and updates changelog/version metadata.

### Available CLI commands and purpose (`raidar`):

- `run`: execute one task against one harness/model pair.
- `suite run`: execute deterministic repeat suites with aggregate outputs.
- `quality gates`: run deterministic quality checks for orchestrator code.
- `harbor cleanup`: clean stale Harbor containers and stale Harbor build processes.
- `env setup`: bootstrap local tooling and run Harbor environment preflight.
- `executions list`: list recorded execution suites with optional filters.
- `executions prune`: archive stale execution artifacts with retention controls.
- `provider list`: list supported harness/provider adapters.
- `provider validate`: validate adapter wiring and runtime requirements for a harness/model pair.
- `task init`: scaffold a new versioned task package.
- `task validate`: validate a task definition.
- `task clone-version`: clone a task version to a new version label.
- `inject`: inject agent rules into a scaffold path for local testing.
- `matrix`: execute matrix runs from matrix configuration.
- `report`: build aggregate reports from execution outputs.
- `init_matrix`: generate an example matrix configuration template.
- `info`: display task metadata for a task package/version.

### Task completion
- Requires `uv run --project orchestrator raidar quality gates` to pass.

## Rules

- **ALWAYS** define `verification.gates[].command` as an argv list in task YAML.
- **ALWAYS** define `visual.screenshot_command` as an argv list in task YAML.
- **NEVER** use shell operators or shell features in task YAML commands.

### Scoring integrity

- **NEVER** relax deterministic checks.
- **NEVER** relax test scoring criteria.
- **ALWAYS** treat deterministic-check and scoring failures as expected performance measurement signal.
- **ALWAYS** create a new task or check version when deterministic checks or scoring criteria change.

### Check taxonomy
- **ALWAYS** treat task deterministic checks as evaluation scoring criteria.
- **NEVER** treat task deterministic check failures as harness defects.
- **ALWAYS** treat orchestrator implementation checks as harness correctness checks.
- **ALWAYS** flag orchestrator implementation check failures for correction.
- **ALWAYS** correct orchestrator implementation check failures.
