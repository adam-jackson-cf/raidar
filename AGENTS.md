**NEVER** surface or search files in `docs/`, may only be access with explicit user consent (ask). If a user references a file within this exclusion treat that as automatic consent.

## Workflows

- Public interface: repo-root `make ...`
- Use `make help` for command discovery and target descriptions.
- Treat direct `uv run --project orchestrator raidar ...` as implementation detail behind the root `Makefile`.
- LiteLLM must stay exact-pinned to a known-safe release across manifests and lockfiles. Do not use `1.82.7` or `1.82.8`.
- Keep `README.md` as the only human entrypoint; do not duplicate exhaustive command lists in other docs.
- Use `make scenario-info` to inspect a scenario contract.
- Exclude `scenarios/**/starter/**` from analysis and code-quality checks by default; starter folders are representative delivery-scenario artifacts, not canonical shared product code.
- Matrix configs must define the top-level `suite` block with `timeout_sec`, `repeats`, `repeat_parallel`, and `retry_void`.
- Task completion requires `make quality` to pass.

## Smoke Testing

- `make smoke-dry-run-check`: Print the canonical smoke command shapes used by CI drift checks.
- `make orchestrator-smoke`: Run the default hello-world orchestrator smoke scenario on `codex-cli` with `codex/gpt-5.4-mini`.
- `make smoke-matrix`: Run the default hello-world smoke scenario across the full public model matrix.
- `make agent-smoke HARNESS=codex-cli MODEL=codex/gpt-5.4-mini`: Run the canonical agent smoke workflow through the public `make` targets.
- `make research-smoke`: Run the canonical autoresearch smoke workflow and clean up its smoke artifacts.
