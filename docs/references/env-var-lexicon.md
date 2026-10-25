# Environment settings

Start with the [root README](../../README.md#install) for setup and `make help`
for supported public options. This reference identifies selected settings and
their consuming owners; it is not an exhaustive command catalogue. The former
scenario-era `EVAL_*` tuning families are retired and do not configure current
experiment deadlines, gates or scoring.

## Credentials

Credential preflight is implemented in
`orchestrator/src/raidar/engine/job/credentials.py`; host-to-agent wiring is owned
by `engine/trial/agent_config.py` and the agent adapters.

- Codex accepts file-backed ChatGPT authentication or `OPENAI_API_KEY`, selected
  by `CODEX_AUTH_MODE=auto|chatgpt|api`. `auto` prefers file-backed login.
  `subject/agent/codex_auth.py` resolves it. `CODEX_HOME` selects an alternate
  Codex credential directory when supplied.
- Claude Code preflight accepts `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or
  `CLAUDE_CODE_OAUTH_TOKEN`. `CLAUDE_FORCE_OAUTH=1` requires the OAuth token.
  `CLAUDE_CODE_API_KEY` is not a supported execution credential alias.
- Other agents use their registered credential requirements. A credential alone
  does not establish runtime-pack compatibility or validate a model pairing.

Secret values must never be printed. Check presence only. Use
[`orchestrator/.env.example`](../../orchestrator/.env.example) for key names;
README explains Infisical and dotenv selection. Do not place credentials in
experiment files or locks.

## Runtime settings and owners

| Setting | Effect | Consuming owner |
|---|---|---|
| `RAIDAR_TARGET` | Target selection when the public `TARGET` option is absent; otherwise the targets file default applies | `engine/target/config.py` |
| `RAIDAR_VAR` | Relocates generated jobs, stores, caches and scratch defaults | `contract/paths.py`, root `Makefile` |
| `RAIDAR_TOOL_CACHE` | Overrides the host measurement-tool cache | `subject/environment/python/provisioner.py` |
| `RAIDAR_LIVENESS` | `enforce` (default), `observe`, or `off` | `engine/health/liveness.py` |
| `RAIDAR_LIVENESS_IDLE_S` | Idle window, default 600 seconds | `engine/health/liveness.py` |
| `RAIDAR_LIVENESS_INTERVAL_S` | Sample interval, default 15 seconds | `engine/health/liveness.py` |

Owner paths in the table are relative to `orchestrator/src/raidar/`.

Experiment agent/verifier deadlines belong to the authored `protocol` fields;
gate settings belong to component configuration; scorer settings belong to the
locked `assessment` configuration. Use explicit experiment overrides through the
public make interface where needed. Setting a retired `EVAL_TIMEOUTS__*`,
`EVAL_GATE__*` or `EVAL_RESOURCE_EFFICIENCY__*` variable has no effect on these
current contracts.
