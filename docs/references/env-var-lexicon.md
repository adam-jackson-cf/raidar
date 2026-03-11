# Environment Variable Lexicon

This is the reference list for repo-visible environment variables that affect Raidar orchestrator behavior.

Use [`orchestrator/.env.example`](/Users/adamjackson/Projects/raidar/orchestrator/.env.example) for the common local setup path. This document is the fuller lexicon, including advanced and internally generated variables.

## User-Set Variables

### Provider credentials

- `OPENAI_API_KEY`
  Purpose: required for Codex harness runs and the OpenAI-backed fast Codex Harbor harness.

- `ANTHROPIC_API_KEY`
  Purpose: primary credential for Claude Code harness runs.

- `CLAUDE_CODE_API_KEY`
  Purpose: alternate Claude Code credential accepted in place of `ANTHROPIC_API_KEY`.

- `CLAUDE_CODE_OAUTH_TOKEN`
  Purpose: optional OAuth token forwarded to the fast Claude harness when that auth mode is used.

- `GEMINI_API_KEY`
  Purpose: required for Gemini harness runs.

- `GOOGLE_API_KEY`
  Purpose: optional alternate Google credential surfaced to the fast Gemini Harbor harness.

- `COPILOT_API_KEY`
  Purpose: required for Copilot CLI harness runs.

- `CURSOR_API_KEY`
  Purpose: required for Cursor CLI harness runs.

- `PI_API_TOKEN`
  Purpose: required for Pi CLI harness runs.

### CLI path overrides

- `CODEX_CLI_PATH`
  Purpose: overrides CLI discovery for the Codex binary. If unset, Raidar falls back to `codex` on `PATH`.

- `CLAUDE_CODE_CLI_PATH`
  Purpose: overrides CLI discovery for the Claude binary. If unset, Raidar falls back to `claude` on `PATH`.

- `GEMINI_CLI_PATH`
  Purpose: overrides CLI discovery for the Gemini binary. If unset, Raidar falls back to `gemini` on `PATH`.

- `COPILOT_CLI_PATH`
  Purpose: overrides CLI discovery for the Copilot binary. If unset, Raidar falls back to `copilot` on `PATH`.

- `CURSOR_CLI_PATH`
  Purpose: overrides CLI discovery for the Cursor binary. If unset, Raidar falls back to `cursor` on `PATH`.

- `PI_CLI_PATH`
  Purpose: overrides CLI discovery for the Pi binary. If unset, Raidar falls back to `pi` on `PATH`.

### Smoke-mode controls

- `HARBOR_SMOKE_FAST`
  Purpose: enables smoke fast mode. When set to a truthy value (`1`, `true`, `yes`, `on`), Raidar switches supported harnesses to repo-local fast Harbor agents and enables fast-only `PYTHONPATH` wiring.
  Typical source: set by [`scripts/checks/run-agent-smoke.sh`](/Users/adamjackson/Projects/raidar/scripts/checks/run-agent-smoke.sh) when `--fast` is used.

- `HARBOR_SMOKE_FAST_REUSE_IMAGE`
  Purpose: enables fast image reuse for smoke runs. Only has effect if `HARBOR_SMOKE_FAST` is also enabled.

- `HARBOR_SMOKE_FAST_IMAGE_PREFIX`
  Purpose: overrides the Docker image repository prefix used for fast-mode reusable task images.
  Default: `ts-ui-eval-smoke-fast`

### Google / Vertex AI passthrough

- `GOOGLE_APPLICATION_CREDENTIALS`
  Purpose: forwarded into the fast Gemini Harbor harness for Google auth flows that rely on a credentials file.

- `GOOGLE_CLOUD_PROJECT`
  Purpose: forwarded into the fast Gemini Harbor harness for Vertex AI configuration.

- `GOOGLE_CLOUD_LOCATION`
  Purpose: forwarded into the fast Gemini Harbor harness for Vertex AI region selection.

- `GOOGLE_GENAI_USE_VERTEXAI`
  Purpose: forwarded into the fast Gemini Harbor harness to force Vertex-backed Gemini execution.

### Evaluation tuning overrides

These are optional runtime tuning variables consumed by [`orchestrator/src/raidar/config.py`](/Users/adamjackson/Projects/raidar/orchestrator/src/raidar/config.py). They are not required for normal setup, but they do alter scoring and timeout behavior.

- `EVAL_TIMEOUTS__BUILD`
- `EVAL_TIMEOUTS__TYPECHECK`
- `EVAL_TIMEOUTS__TEST`
- `EVAL_TIMEOUTS__GATE`
- `EVAL_TIMEOUTS__SCREENSHOT`
- `EVAL_TIMEOUTS__IMAGE_COMPARE`
- `EVAL_TIMEOUTS__COMMAND_DEFAULT`
  Purpose: override command and verifier timeouts.

- `EVAL_LLM_JUDGE__MODEL`
- `EVAL_LLM_JUDGE__MAX_TOKENS`
- `EVAL_LLM_JUDGE__MAX_SOURCE_CHARS`
- `EVAL_LLM_JUDGE__MAX_RETRIES`
  Purpose: tune the acceptance LLM judge behavior.

- `EVAL_WEIGHTS__FUNCTIONAL`
- `EVAL_WEIGHTS__ACCEPTANCE`
- `EVAL_WEIGHTS__VISUAL`
- `EVAL_WEIGHTS__VERIFICATION_STABILITY`
  Purpose: override scoring weights.

- `EVAL_VERIFICATION_STABILITY__MAX_GATE_FAILURES`
- `EVAL_VERIFICATION_STABILITY__REPEAT_PENALTY`
  Purpose: tune verification stability scoring.

- `EVAL_GATE__MAX_FAILURES`
- `EVAL_GATE__MAX_OUTPUT_LENGTH`
  Purpose: tune gate watcher termination thresholds.

- `EVAL_VISUAL__ODIFF_THRESHOLD`
- `EVAL_VISUAL__SIMILARITY_THRESHOLD`
  Purpose: tune visual comparison strictness.

- `EVAL_RESOURCE_EFFICIENCY__MAX_UNCACHED_TOKENS`
- `EVAL_RESOURCE_EFFICIENCY__MAX_COMMANDS`
- `EVAL_RESOURCE_EFFICIENCY__MAX_FAILED_COMMANDS`
- `EVAL_RESOURCE_EFFICIENCY__MAX_EXTRA_VERIFICATION_ROUNDS`
- `EVAL_RESOURCE_EFFICIENCY__MAX_REPEAT_FAILURES`
- `EVAL_RESOURCE_EFFICIENCY__TOKEN_WEIGHT`
- `EVAL_RESOURCE_EFFICIENCY__COMMAND_WEIGHT`
- `EVAL_RESOURCE_EFFICIENCY__FAILURE_WEIGHT`
- `EVAL_RESOURCE_EFFICIENCY__VERIFICATION_ROUND_WEIGHT`
- `EVAL_RESOURCE_EFFICIENCY__REPEAT_FAILURE_WEIGHT`
  Purpose: tune the resource-efficiency score calculation.

## Internal / Generated Variables

- `AGENTIC_EVAL_SECRET_FILE_<NAME>`
  Purpose: internal variable family generated by the runner so Harbor fast agents can read secrets from mounted files instead of plain env values.
  Examples: `AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY`, `AGENTIC_EVAL_SECRET_FILE_GEMINI_API_KEY`
  Notes: these are not intended for manual `.env` authoring.

## Observed Gaps Closed

The previous [`orchestrator/.env.example`](/Users/adamjackson/Projects/raidar/orchestrator/.env.example) omitted:

- smoke-mode flags
- Copilot, Cursor, and Pi credentials / CLI-path overrides
- `CLAUDE_CODE_OAUTH_TOKEN`
- `GOOGLE_API_KEY`
- Google / Vertex AI passthrough variables
- any pointer to the `EVAL_*` tuning surface
