# Command Surface

This repo is migrating to a scenario/experiment command model.

## Target Public Surface

Use the repo-root `Makefile` for common workflows.

```bash
make env-setup
make agent-list
make agent-validate AGENT=codex-cli MODEL=codex/gpt-5.2-high
make scenario-info SCENARIO_DIR=scenarios/homepage-implementation/v001
make scenario-validate SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml
make experiment-run SCENARIO=... AGENT=... MODEL=...
make matrix-run SCENARIO=... CONFIG=matrix.yaml
make experiments-list [EVALUATION_PROFILE=...] [LIMIT=...]
make experiments-prune [KEEP_PER_MODEL=1]
make quality
```

## Internal Surfaces

These remain implementation details behind the public surface:

- `uv run --project orchestrator raidar ...`
- `scripts/run-provider-smoke.sh`
- `scripts/run-codex-baselines.sh`

## Notes

- Scenario fixtures now live under `scenarios/` and use `scenario.yaml`.
- Experiment artifacts now live under `experiments/`.
- The current `Makefile` still exposes legacy target names. Keep docs and tests aligned to the migrated contract, but treat that mismatch as an outstanding production follow-up.
