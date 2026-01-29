# Handoff: Enable secure Codex auth + run full Harbor eval

## Starting Prompt

You19re picking up the agentic-eval project after we introduced `.env`-based secret loading and documented the Codex OAuth plan. Your goals:

1. Finalize the Codex auth experience:
   - Add user-facing docs/instructions covering both API-key and OAuth flows (see `docs/references/OAuth-improvement.md` for the plan).
   - Consider providing a helper script or CLI command that guides users through exporting `CODEX_OAUTH_TOKEN` or mounting `~/.codex` if we decide to support that.

2. Conduct an authenticated Harbor run:
   - Populate `orchestrator/.env` with a valid `CODEX_API_KEY` (user provided) and confirm `uv run eval-orchestrator run ...` completes without "Harbor exited with code 2/Harbor not installed".
   - Capture the resulting artifacts and note any issues (e.g., Docker requirements, missing binaries).

Start by reviewing `orchestrator/README.md` and `docs/references/OAuth-improvement.md`, then open `scripts/setup.sh` to confirm prerequisites. After updating docs/scripts, run the Harbor test and verify the new results folder under `orchestrator/results/`.

## Relevant Files

- `docs/references/OAuth-improvement.md` 6 contains the phased plan for OAuth support; expand/execute Phase 1 tasks.
- `orchestrator/README.md` 6 now mentions `.env`; extend it with explicit auth instructions or helper workflow.
- `scripts/setup.sh` 6 currently sets up uv + Harbor; update if additional auth checks/scripts are added.
- `orchestrator/src/agentic_eval/cli.py` 6 loads `.env`; modify if you add new env handling or helper commands.
- `orchestrator/results/cdc46157*` 6 latest Harbor attempt (failed at code 2); use as reference when validating the new successful run.

## Key Context

- `.env` loading is automatic; secrets should live in `orchestrator/.env`, which is git-ignored.
- Codex adapter still requires either `CODEX_API_KEY` or `CODEX_OAUTH_TOKEN`; no automatic OAuth handoff yet.
- Harbor CLI is installed via `uv tool install harbor`; Docker must be running locally for real executions.
- Last Harbor run terminated with exit code 2, so we still need a validated success case once credentials are present.
