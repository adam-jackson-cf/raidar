---
name: "raidar-smoke"
description: "Use when validating Raidar agent health or run mechanics with smoke trials."
---

# Task

## Select and run

- Check the selected Docker target and credentials through the public make targets.
- Default single trial: `make orchestrator-smoke TARGET=<name>`.
- Explicit agent: `make agent-smoke TARGET=<name> AGENT=codex PROVIDER=openai MODEL=gpt-6-luna REASONING_EFFORT=low`.
- Claude Code: `make agent-smoke TARGET=<name> AGENT=claude-code`;
  defaults to claude-haiku-4-5 with no reasoning effort. Explicit alternatives must pass
  agent/model validation and follow the experiment design; Haiku is not a universal restriction.
- Every pack agent: `make smoke-agents TARGET=<name>`; SMOKE_ARM=codex or claude selects one.
- Use the experiment-execution procedure for a directly executed multi-checkpoint mechanics smoke.
- Do not set implementation environment variables to reproduce smoke-target internals.
  Agent versions default to pack pins; an empty REASONING_EFFORT= unsets the field, while '' is an empty string.

## Validation and output

- Require valid trials, no health violations and /app changes at every executed step before calling a smoke clean.
- Report job path, selected agent/model/effort, target and terminal outcome.
- Label all smoke results operational and not citable.
