# Adding a New Harness

Use this flow to add a new harness cleanly and keep run outputs comparable. `AgentSpec` means `harness + model`; this guide covers the harness half of that pairing.

## 1. Extend the Harness Registry

1. Add enum entry in `orchestrator/src/raidar/agents/config.py`.
2. Implement adapter in `orchestrator/src/raidar/agents/adapters/`.
3. Register the adapter in `orchestrator/src/raidar/agents/adapters/registry.py`.
4. Add a `HarnessDefinition` entry in `orchestrator/src/raidar/harness/definitions.py`.

Harness adapter responsibilities:

- validate provider/model prefix compatibility.
- validate required local CLI binaries and environment prerequisites.
- emit Harbor harness name, model argument, and extra Harbor args.

Harness definition responsibilities:

- declare the injected rule filename.
- declare harness artifact files and the final workspace archive.
- declare trace and command parser identifiers.
- declare token usage support and parser requirements.
- declare concrete harness execution requirements as capabilities.
- declare npm package metadata when the harness is installed into the task image.

## 2. Wire CLI and Rules Mapping

1. Ensure CLI choices include the new harness where relevant (`harness validate`, `inject`, `matrix`, and experiment commands).
2. Set `rule_filename` in the harness definition.

Rule injection is resolved through `orchestrator/src/raidar/agents/rules.py`, which reads the registered harness definition.

## 3. Ensure Scenario Rules Compatibility

For each active scenario revision, add the harness-specific rules file to:

- `scenarios/<scenario>/v###/rules/`

The runner injects exactly one rules file based on the selected harness definition.

## 4. Declare Runtime Requirements

Harness execution requirements are inventory requirements, not scoring behavior. Put only concrete dependencies in the harness definition:

```python
execution_requirements=CapabilityRequirements(
    runtimes={"node": ">=20"},
    tools={"git": ">=2"},
)
```

Those requirements are merged with scenario, scorer, and verifier requirements in the effective run contract. Runtime stack validation checks the selected task image can satisfy the combined requirement set.

When a harness is distributed through npm, set `npm_package` and `npm_version_probe` so the task image can install the same CLI family used by local validation.

## 5. Trace Parsing Coverage

If log format differs, wire the parser identifiers declared in the harness definition to the parser implementation and add tests so process metrics and trace events are extracted consistently.

Relevant runtime surfaces:

- `orchestrator/src/raidar/harness/definitions.py`: harness metadata.
- `orchestrator/src/raidar/harness/trace_log.py`: trace event extraction.
- `orchestrator/src/raidar/runtime/usage_metrics.py`: token and usage extraction.
- `orchestrator/src/raidar/runtime/command_records.py`: command record normalization.

Harness integration expectations for scorer-driven evaluation:

- Do not change scenario scoring behavior in adapters; scorer assignment is scenario-defined via `scenario.yaml -> scorers[]`.
- Ensure adapter output still allows deterministic verifier execution so `scorecard.metric_scores[]` and `scorecard.scorer_results[]` are written.
- Keep run metadata parity so `evaluation_profile`, scorer outputs, metric outputs, and runtime contract metadata remain comparable across `AgentSpec`s.

## 6. Validate End-to-End

```bash
make harness-validate HARNESS=<harness> PROVIDER=<provider> MODEL=<model>
make runtime-stack-scenario-smoke \
  SCENARIO=scenarios/hello-world-smoke/v001/scenario.yaml \
  HARNESS=<harness> \
  PROVIDER=<provider> \
  MODEL=<model>
make experiment-run \
  SCENARIO=scenarios/hello-world-smoke/v001/scenario.yaml \
  HARNESS=<harness> \
  PROVIDER=<provider> \
  MODEL=<model>
```

Check outputs in:

- `experiments/benchmarks/<experiment-id>/experiment-summary.json`
- `experiments/benchmarks/<experiment-id>/runs/*/run.json`

Verify these fields are present and consistent:

- `config.evaluation_profile` in run and experiment config blocks.
- `scores.metric_scores[]` in `run.json` and verifier scorecard artifacts.
- `scores.scorer_results[]` in `run.json`.
- `scores.metadata.harbor.cache.contract.cache_payload.harness`.
- `scores.metadata.harbor.cache.required_capabilities`.
