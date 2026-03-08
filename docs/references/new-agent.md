# Adding a New Agent

Use this flow to add a new agent cleanly and keep run outputs comparable.

## 1. Extend the Agent Registry

1. Add enum entry in `orchestrator/src/raidar/agents/config.py`.
2. Implement adapter in `orchestrator/src/raidar/agents/adapters/`.
3. Register the adapter in `orchestrator/src/raidar/agents/adapters/registry.py`.

Adapter responsibilities:
- validate provider/model prefix compatibility.
- validate required CLI binaries and environment prerequisites.
- emit Harbor agent name, model argument, and extra Harbor args.

## 2. Wire CLI and Rules Mapping

1. Ensure CLI choices include the new agent where relevant (`experiment run`, `agent validate`, `inject`, `matrix`).
2. Add rule filename mapping in `orchestrator/src/raidar/agents/rules.py` (`SYSTEM_RULES`).

## 3. Ensure Scenario Rules Compatibility

For each active scenario revision, add the agent-specific rules file to:
- `scenarios/<scenario>/v###/rules/`

The runner injects exactly one rules file based on `SYSTEM_RULES`.

## 4. Trace Parsing Coverage

If log format differs, extend `orchestrator/src/raidar/parser/trace_log.py` and add tests so process metrics and trace events are extracted consistently.

Agent integration expectations for metric-driven evaluation:
- Do not change scenario metric behavior in adapters; metric assignment is scenario-defined via `scenario.yaml -> metrics[]`.
- Ensure adapter output still allows deterministic verifier execution so `scorecard.metric_results[]` is written.
- Keep run metadata parity so `evaluation_profile` and metric outputs remain comparable across agents.

## 5. Validate End-to-End

```bash
make agent-validate AGENT=<agent> MODEL=<provider/model>
make experiment-run \
  SCENARIO=scenarios/hello-world-smoke/v001/scenario.yaml \
  AGENT=<agent> \
  MODEL=<provider/model>
```

Check outputs in:
- `experiments/<experiment-id>/experiment-summary.json`
- `experiments/<experiment-id>/runs/*/run.json`

Verify these fields are present and consistent:
- `config.evaluation_profile` in run and experiment config blocks.
- `config.metrics` in experiment config.
- `scores.metric_results[]` in `run.json` and verifier scorecard artifacts.
