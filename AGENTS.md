**NEVER** surface or search files in `docs/references`, may only be access with explicit user consent (ask). If a user references a file within this exclusion treat that as automatic consent.

## Structure

## Workflow
- **ALWAYS** define `verification.gates[].command` as an argv list in task YAML.
- **ALWAYS** define `visual.screenshot_command` as an argv list in task YAML.
- **NEVER** use shell operators or shell features in task YAML commands.

## Workflows

## Task completion
- Requires `scripts/run-ci-quality-gates.sh` to pass.

## Scoring integrity
- **NEVER** relax deterministic checks.
- **NEVER** relax test scoring criteria.
- **ALWAYS** treat deterministic-check and scoring failures as expected performance measurement signal.
- **ALWAYS** create a new task or check version when deterministic checks or scoring criteria change.

## Check taxonomy
- **ALWAYS** treat task deterministic checks as evaluation scoring criteria.
- **NEVER** treat task deterministic check failures as harness defects.
- **ALWAYS** treat orchestrator implementation checks as harness correctness checks.
- **ALWAYS** flag orchestrator implementation check failures for correction.
- **ALWAYS** correct orchestrator implementation check failures.
