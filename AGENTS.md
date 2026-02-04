**NEVER** surface or search files in `docs/reference`, may only be access with explicit user consent (ask). If a user references a file within this exclusion treat that as automatic consent.

## Structure

## Workflow
- **ALWAYS** define `verification.gates[].command` as an argv list in task YAML.
- **ALWAYS** define `visual.screenshot_command` as an argv list in task YAML.
- **NEVER** use shell operators or shell features in task YAML commands.

## Workflows

## Task completion
- Requires `scripts/run-ci-quality-gates.sh` to pass.
