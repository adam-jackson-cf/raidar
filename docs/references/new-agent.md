# Adding a New Agent (Harness + Model)

Use this flow to add a new harness cleanly and keep run outputs comparable.

## 1. Extend Harness Registry

1. Add enum entry in `orchestrator/src/raidar/harness/config.py`.
2. Implement adapter in `orchestrator/src/raidar/harness/adapters/`.
3. Register adapter in `orchestrator/src/raidar/harness/adapters/registry.py`.

Adapter responsibilities:
- validate provider/model prefix compatibility.
- validate required CLI binaries and environment prerequisites.
- emit Harbor agent name, model argument, and extra Harbor args.

## 2. Wire CLI and Rules Mapping

1. Ensure CLI choices include the new agent where relevant (`run`, `suite run`, `provider validate`, `inject`, `matrix`).
2. Add rule filename mapping in `orchestrator/src/raidar/harness/rules.py` (`SYSTEM_RULES`).

## 3. Ensure Task Rules Compatibility

For each active task version, add the agent-specific rules file to:
- `tasks/<task>/v###/rules/`

The runner injects exactly one ruleset file based on `SYSTEM_RULES` mapping.

## 4. Session Parsing Coverage

If log format differs, extend `orchestrator/src/raidar/parser/session_log.py` and add tests so process metrics/events are extracted consistently.

Agent integration expectations for module-driven evaluation:
- Do not change task metric behavior in adapters; metric assignment is task-defined via `task.yaml -> metrics.modules[]`.
- Ensure adapter output still allows deterministic verifier execution so `scorecard.modules[]` is written.
- Keep run metadata parity so `metric_profile` and module outputs remain comparable across harnesses.

## 5. Validate End-to-End

```bash
cd orchestrator
uv run raidar provider validate --agent <agent> --model <provider/model>
uv run raidar run \
  --task ../tasks/hello-world-smoke/v001/task.yaml \
  --agent <agent> \
  --model <provider/model>
```

Check outputs in:
- `evals/<suite-id>/suite-summary.json`
- `evals/<suite-id>/runs/*/run.json`

Verify these fields are present and consistent:
- `config.metric_profile` in run/suite config blocks.
- `config.metric_modules` in suite config.
- `scores.modules[]` in `run.json` / verifier scorecard artifacts.
