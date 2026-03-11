**NEVER** surface or search files in `docs/`, may only be access with explicit user consent (ask). If a user references a file within this exclusion treat that as automatic consent.

## Workflows

- Public interface: repo-root `make ...`
- Use `make help` for command discovery and target descriptions.
- Treat direct `uv run --project orchestrator raidar ...` as implementation detail behind the root `Makefile`.
- Keep `README.md` as the only human entrypoint; do not duplicate exhaustive command lists in other docs.
- Use `make scenario-info` to inspect a scenario contract.
- Matrix configs must define the top-level `suite` block with `timeout_sec`, `repeats`, `repeat_parallel`, and `retry_void`.
- Task completion requires `make quality` to pass.
