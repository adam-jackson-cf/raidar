# Codex OAuth Improvement Plan

Goal: support Codex CLI usage in Harbor with secure non-interactive authentication guidance.

## Current State

- Codex adapter requires `OPENAI_API_KEY` at validation time.
- OAuth token injection is not yet a first-class orchestrator workflow.
- Harbor runs execute inside containerized environments and do not inherit host auth caches automatically.

## Constraints

1. Interactive OAuth inside Harbor containers is not practical.
2. Long-lived tokens should not be persisted in repository artifacts.
3. Any auth workflow must remain deterministic for benchmark repeatability.

## Recommended Path

### Phase 1 (Documentation + Safe Workflow)

1. Document short-lived token workflow for local export before run.
2. Keep primary documented path as `OPENAI_API_KEY` for Codex harness stability.
3. Add explicit teardown guidance (unset env vars after runs).

### Phase 2 (Optional UX)

1. Add helper command/script for auth preflight checks.
2. Emit clear adapter errors that differentiate missing API key vs missing OAuth token.

### Phase 3 (Advanced)

1. Evaluate secure credential mount support once Harbor exposes stable volume controls.
2. Add sanitized auth metadata in run artifacts (method only, never token values).

## Validation Checklist

- `uv run eval-orchestrator provider validate --agent codex-cli --model codex/gpt-5.2-high`
- `./scripts/run-provider-smoke.sh --agent codex-cli --model codex/gpt-5.2-high`

## Status

- Phase 1: partially complete (core env-based auth guidance exists).
- Phase 2+: pending.
