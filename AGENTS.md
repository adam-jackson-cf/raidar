**NEVER** surface or search files in `docs/`, may only be access with explicit user consent (ask). If a user references a file within this exclusion treat that as automatic consent.

## Workflows

- Public interface: repo-root `make ...`
- Use `make help` for command discovery and target descriptions.
- Treat direct `uv run --project orchestrator raidar ...` as implementation detail behind the root `Makefile`.
- LiteLLM must stay exact-pinned to a known-safe release across manifests and lockfiles. Do not use `1.82.7` or `1.82.8`.
- Keep `README.md` as the only human entrypoint; do not duplicate exhaustive command lists in other docs.
- Use `make scenario-info` to inspect a scenario contract.
- Use `make scenario-clone-revision SCENARIO_DIR=scenarios/<scenario-id> FROM_REVISION=v001 [TO_REVISION=v002]` to create a new revision inside an existing scenario root.
- Use `make scenario-init` only for brand-new scenario roots. If the intent is another revision of an existing scenario, do not create a sibling scenario directory; clone the revision inside the existing root instead.
- Exclude `scenarios/**/starter/**` from analysis and code-quality checks by default; starter folders are representative delivery-scenario artifacts, not canonical shared product code.
- Treat `scenarios/` and `experiments/` as build-generated runtime artifacts by default: exclude them from quality checks unless the request explicitly asks to review/analyze them.
- Matrix configs live under `matrices/` and must define `matrix.id`, `matrix.scenario`, `matrix.experiment` with `timeout_sec`, `repeats`, `repeat_parallel`, and `retry_void`, plus `matrix.entries` entries with `id`, `scenario_revision`, and nested `agent` fields for `harness`, `provider`, `model`, and optional `reasoning_effort`.
- Task completion requires `make quality` to pass.

## Known Matrix Configs

- `matrices/homepage-v001-codex-oauth.yaml`: Homepage implementation revision `v001` Codex-only benchmark using OAuth/session auth across `gpt-5.5-low`, `gpt-5.5-medium`, `gpt-5.4-mini-low`, and `gpt-5.3-codex-spark-low`.

## Smoke Testing

- Public smoke targets run through the repo-local Harbor agent path by default. Use `make orchestrator-smoke`, `make smoke-matrix`, and `make agent-smoke` without setting fast-mode env vars manually.
- Use the public `make` targets for smoke runs rather than invoking Raidar implementation commands directly.
- Small single smoke on low-cost Codex: `make agent-smoke HARNESS=codex-cli MODEL=codex/gpt-5.4-mini-low TIMEOUT_SEC=300`
- Full default smoke matrix: `make smoke-matrix`
- Codex-only default smoke matrix: `make smoke-matrix SMOKE_MATRIX_SELECTOR=codex`
- Gemini-only default smoke matrix: `make smoke-matrix SMOKE_MATRIX_SELECTOR=gemini`
- Claude-only default smoke matrix: `make smoke-matrix SMOKE_MATRIX_SELECTOR=claude`

## Branch Syncing

- The canonical autoresearch removal commit is `d1973d6` `refactor: remove autoresearch surface from main`.
- When pulling `main` changes into `feat/autoresearch-v3`, prefer cherry-picking specific wanted commits instead of merging all of `main`.
- Do not cherry-pick `d1973d6` into `feat/autoresearch-v3`.
- Do not cherry-pick later cleanup commits that assume `auto_researcher/` is absent from `main`, including CI or workflow updates that remove direct `auto_researcher` setup steps.
- If a later `main` commit depends on `d1973d6` and removes or rewires autoresearch files, skip that commit too unless the feature branch is explicitly being reconciled with the removal.
