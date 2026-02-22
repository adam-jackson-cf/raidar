# Architecture Review (Current)

This snapshot reflects the refactored execution/task model:
- task-level versioning (`tasks/<task>/v###`).
- prompt artifacts externalized from `task.yaml`.
- single ruleset per task version.
- unified execution root (`executions/...`).

## Domain Boundaries

- `orchestrator/src/agentic_eval/cli.py`: command surface and suite orchestration.
- `orchestrator/src/agentic_eval/runner.py`: workspace prep, Harbor execution, scoring assembly.
- `orchestrator/src/agentic_eval/schemas/*.py`: task and scorecard contracts.
- `orchestrator/src/agentic_eval/storage.py`: aggregate/report helpers over execution outputs.
- `tasks/<task>/v###`: benchmark definition, rules, prompt artifacts, scaffold root.

## Strengths

- Deterministic output topology allows suite-level reproducibility.
- Task versioning isolates benchmark iterations cleanly.
- Task config now separates implementation artifacts from execution config.

## Current Risks

1. `runner.py` remains a large orchestration unit; future backend changes have wide blast radius.
2. Matrix workflows are still synchronous and can accumulate execution artifacts quickly.
3. Task-local scaffold directories can still drift if edited in-place after baselines are established.

## Recommended Next Steps

1. Extract runner phases into smaller services (workspace, Harbor, artifact persistence, scoring).
2. Add explicit scaffold freeze workflow for version bumps (`v001 -> v002`).
3. Add retention policy docs/automation for `executions` growth.
