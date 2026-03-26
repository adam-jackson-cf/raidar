# Adding a New Harness

Use this flow to add a new harness cleanly and keep run outputs comparable. `AgentSpec` means `harness + model`; this guide covers the harness half of that pairing.

## 1. Extend the Harness Registry

1. Add enum entry in `orchestrator/src/raidar/agents/config.py`.
2. Implement adapter in `orchestrator/src/raidar/agents/adapters/`.
3. Register the adapter in `orchestrator/src/raidar/agents/adapters/registry.py`.

Harness adapter responsibilities:
- validate provider/model prefix compatibility.
- validate required CLI binaries and environment prerequisites.
- emit Harbor harness name, model argument, and extra Harbor args.

## 2. Wire CLI and Rules Mapping

1. Ensure CLI choices include the new harness where relevant (`harness validate`, `inject`, `matrix`, and experiment commands).
2. Add rule filename mapping in `orchestrator/src/raidar/agents/rules.py` (`SYSTEM_RULES`).

## 3. Ensure Scenario Rules Compatibility

For each active scenario revision, add the harness-specific rules file to:
- `scenarios/<scenario>/v###/rules/`

The runner injects exactly one rules file based on `SYSTEM_RULES`.

## 4. Trace Parsing Coverage

If log format differs, extend `orchestrator/src/raidar/parser/trace_log.py` and add tests so process metrics and trace events are extracted consistently.

Harness integration expectations for metric-driven evaluation:
- Do not change scenario metric behavior in adapters; metric assignment is scenario-defined via `scenario.yaml -> metrics[]`.
- Ensure adapter output still allows deterministic verifier execution so `scorecard.metric_results[]` is written.
- Keep run metadata parity so `evaluation_profile` and metric outputs remain comparable across `AgentSpec`s.

## 5. Validate End-to-End

```bash
make harness-validate HARNESS=<harness> MODEL=<provider/model>
make experiment-run \
  SCENARIO=scenarios/hello-world-smoke/v001/scenario.yaml \
  HARNESS=<harness> \
  MODEL=<provider/model>
```

Check outputs in:
- `experiments/benchmarks/<experiment-id>/experiment-summary.json`
- `experiments/benchmarks/<experiment-id>/runs/*/run.json`

Verify these fields are present and consistent:
- `config.evaluation_profile` in run and experiment config blocks.
- `config.metrics` in experiment config.
- `scores.metric_results[]` in `run.json` and verifier scorecard artifacts.
