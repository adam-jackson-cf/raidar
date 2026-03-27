**NEVER** surface or search files in `docs/`, may only be access with explicit user consent (ask). If a user references a file within this exclusion treat that as automatic consent.

## Workflows

- Public interface: repo-root `make ...`
- Use `make help` for command discovery and target descriptions.
- Treat direct `uv run --project orchestrator raidar ...` as implementation detail behind the root `Makefile`.
- LiteLLM must stay exact-pinned to a known-safe release across manifests and lockfiles. Do not use `1.82.7` or `1.82.8`.
- Keep `README.md` as the only human entrypoint; do not duplicate exhaustive command lists in other docs.
- Use `make scenario-info` to inspect a scenario contract.
- Exclude `scenarios/**/starter/**` from analysis and code-quality checks by default; starter folders are representative delivery-scenario artifacts, not canonical shared product code.
- Treat `scenarios/` and `experiments/` as build-generated runtime artifacts by default: exclude them from quality checks unless the request explicitly asks to review/analyze them.
- Matrix configs must define the top-level `suite` block with `timeout_sec`, `repeats`, `repeat_parallel`, and `retry_void`.
- Task completion requires `make quality` to pass.

## Smoke Testing

- Harness-led `raidar` ExecPlan smoke runs must use `/Users/adamjackson/Projects/execplan-executor/scripts/setup_raidar_smoke_worktree.sh` to create the fresh detached worktree, copy the finalized ExecPlan package, and symlink `orchestrator/.env` from the canonical `raidar` checkout before launch.
- Canonical setup shape: `/Users/adamjackson/Projects/execplan-executor/scripts/setup_raidar_smoke_worktree.sh --worktree-path /Users/adamjackson/Projects/raidar-harness-eval-<timestamp> --packet-root /Users/adamjackson/Projects/raidar/.plan/create-execplan/<artifact-id>`
- Maintain `/Users/adamjackson/Projects/execplan-executor/smoke-findings.md` during the smoke loop as a concise `issue > action` log of real blockers and corrective actions.
- Add a new `smoke-findings.md` entry only after a blocker or contradiction has been confirmed and a concrete action has been taken to address it; do not add entries for routine healthy progress, speculative diagnoses, or unchanged reruns.
- `make smoke-dry-run-check`: Print the canonical smoke command shapes used by CI drift checks.
- `make orchestrator-smoke`: Run the default hello-world orchestrator smoke scenario on `codex-cli` with `codex/gpt-5.4-mini`.
- `make smoke-matrix`: Run the default hello-world smoke scenario across the full public model matrix.
- `make agent-smoke HARNESS=codex-cli MODEL=codex/gpt-5.4-mini`: Run the canonical agent smoke workflow through the public `make` targets.
