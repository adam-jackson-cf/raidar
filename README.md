<div align="center">

<h1>Raidar</h1>

**Controlled experiments on coding agents: does a treatment (rules, skills, hooks, review loops) change what a harness + model delivers on pinned problems?**

![Status](https://img.shields.io/badge/status-active-brightgreen.svg?style=flat-square)
![Python](https://img.shields.io/badge/python-3.12-blue.svg?style=flat-square)
![Runtime](https://img.shields.io/badge/runtime-uv%20%7C%20docker%20%7C%20harbor-lightgrey.svg?style=flat-square)
![Primary CLI](https://img.shields.io/badge/cli-raidar-orange.svg?style=flat-square)

</div>

Raidar runs an **experiment**: the same pinned **problems** under two or more **arms** (a condition built from **components**, on an agent), repeated in counterbalanced **blocks**. Every trial runs through [Harbor](https://pypi.org/project/harbor/) in the runtime pack's Docker image and is graded by a host-only **oracle**. **Scorers** turn stored trials into **Assessments** offline. The analysis reports paired effects against the baseline arm with a verdict of `improved`, `worse`, `inconclusive` or `insufficient`. The review surface shows the runs and their Quality components.

The design lives in [`docs/reboot/requirements.md`](docs/reboot/requirements.md) (frozen v1), [`docs/reboot/implementation-plan.md`](docs/reboot/implementation-plan.md), [`docs/reboot/adr-001.md`](docs/reboot/adr-001.md) and [`docs/reboot/design-memo.md`](docs/reboot/design-memo.md).

> **The scenario epoch is retired.** The scenario and matrix authoring and execution paths were removed at the Phase 1 cutover. `scenarios/`, `matrices/` and `environments/` are read-only history, and nothing reads them. Scenario-epoch experiments under `experiments/` still render in the review surface from their persisted `review-projection.json`. The removed `make` targets (`scenario-*`, `experiment-run`, `matrix-run`, `experiments-*`) refuse and point here.

## Install

Prerequisites:

- `uv`
- Docker with `docker compose` 2.40.1 or newer. OrbStack is the default: `DOCKER_CONTEXT` defaults to `orbstack`.
- Node 18 or newer, for the review surface
- Agent credentials: file-backed Codex auth (`make codex-auth-setup`) or `OPENAI_API_KEY` for codex-cli; `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` for claude-code

```bash
cp orchestrator/.env.example orchestrator/.env
make env-setup
```

For Codex CLI the default auth policy is `CODEX_AUTH_MODE=auto`: it prefers file-backed ChatGPT login from `~/.codex/auth.json` and falls back to `OPENAI_API_KEY`. A login kept in the OS keyring cannot be passed into Harbor, so switch Codex to file-backed credential storage first. `make help` lists every public target.

## Concepts

| Term | What it is | Where it lives |
|---|---|---|
| Experiment | The unit of authoring: problems, agents, conditions, arms, scorers and the analysis plan | `experiment.yaml` (for example `examples/pilot/`) |
| Lock | Content hashes and pinned versions of everything the experiment resolves to. A run refuses a lock that is out of date | `<name>.lock.json` next to the experiment |
| Problem | A pinned, oracle-validated task (one-shot or multi-checkpoint). Its oracle stays on the host and the agent never sees it | `problems/registry/`, imported into `problems/store/` |
| Runtime pack | The agent image (toolchain and pinned harness binaries, no scorer tools) plus the scorer tool lock | `orchestrator/src/raidar/packs/python/` |
| Component | A reusable treatment piece: `rules-file@1`, `skills@1`, `lint-config@1`, `stop-hook@1`, `mcp-server@1`, `harness-settings@1`, `workspace-files@1`, `prompt-wrapper@1`, `review-loop@1` | `orchestrator/src/raidar/components/` |
| Condition and arm | A condition is a list of components. An arm is one condition on one agent. The factor guard keeps arms differing only in the declared factors | `conditions:` and `arms:` |
| Agent | Harness, provider, model, reasoning effort and the pinned harness version | `agents:` |
| Block | One pass over every arm and problem, in counterbalanced order. Blocks supply the paired samples | `analysis.blocks` |
| Run | One execution of a locked experiment: trials, trajectories, attempts, validity checks and N1 phase timings | `runs/<experiment>/<run id>/` |
| Scorer and Assessment | A versioned scorer (`correctness@1`, `code-quality@1`, `resource-usage@1`, `process-compliance@1`) runs offline over stored trials. Each result is an Assessment keyed by its inputs | an Assessment store directory, outside the run |
| Headline policy | How component scores combine into the headline S (`scb-canonical@1`, `scb-canonical-oneshot@1`). The lock pins it | `assessment.headline_policy` |
| Analysis | Paired deltas, confidence intervals, curves, verdicts, exclusions and a sample-size projection against the baseline arm | the `make experiment-analyze` output directory |
| Jev | Decomposed LLM-judge questions. They run in shadow until a policy is calibrated and promoted | `raidar jev ...`; the audit sits in the Assessment store |

## Walkthrough: experiment.yaml to review

The example compares a baseline with a rules-file treatment on the validated one-shot problem the pilot uses. Every step is a public `make` target.

**1. Provide the problem and the pack.** A problem is pinned by reference and must be oracle-validated before any run can use it. `problem-validate` is needed only for a problem without a validation report in `problems/registry/`.

```bash
make problem-import PROBLEM=scb/circuit_eval.checkpoint_1@38d627ecf668
make pack-build
```

**2. Write `experiment.yaml`.** Paths in `files:` are relative to the experiment file.

```yaml
schema_version: 1
id: rules-trial
description: Does an AGENTS.md rules file change correctness on circuit_eval?

problems:
  - ref: scb/circuit_eval.checkpoint_1@38d627ecf668

agents:
  - id: codex-luna
    harness: codex-cli
    provider: openai
    model: gpt-6-luna
    reasoning_effort: low
    version: 0.156.1          # must equal the pack pin

factors: [condition]

conditions:
  - id: baseline
    components: []
  - id: rules
    components:
      - use: rules-file@1
        files: [components/AGENTS.md]

arms:
  - id: baseline
    condition: baseline
    agent: codex-luna
  - id: rules
    condition: rules
    agent: codex-luna

assessment:
  scorers: [correctness@1, code-quality@1, resource-usage@1]
  headline_policy: scb-canonical-oneshot@1

analysis:
  baseline_arm: baseline
  blocks: 5
  seed: 20260930
  min_pairs: 5               # the lock pins these verdict thresholds
  max_exclusion_fraction: 0.2
  alpha: 0.05
```

**3. Validate and lock.** Validation checks the schema, the cross-references, the factor guard, and that every arm's components compile for its harness. The lock pins content hashes and versions. `raidar run` refuses to start if relocking would change the lock.

```bash
make experiment-validate EXPERIMENT=my-exp/experiment.yaml
make experiment-lock EXPERIMENT=my-exp/experiment.yaml
make experiment-plan EXPERIMENT=my-exp/experiment.yaml     # dry run: every trial and its hashes
```

**4. Run.** Each trial runs through Harbor. The run directory records attempts, validity checks (context isolation, no test leak, progression) and N1 phase timings.

```bash
make experiment-execute EXPERIMENT=my-exp/experiment.yaml RUN_ID=first
make perf-check RUN=runs/rules-trial/first                 # N1 overhead budgets
```

If a run is stopped mid-trial, `make harbor-cleanup RUN=runs/rules-trial/first` removes the trial environments it left behind.

**5. Assess.** Scorers run offline over the stored trials. They write to an Assessment store outside the run and use the run's locked `assessment-config.yaml`. `REPLAY=1` reruns existing Assessments and requires byte-identical output.

```bash
make experiment-assess RUN=runs/rules-trial/first STORE=stores/rules-trial \
  SCORERS="correctness@1 code-quality@1 resource-usage@1"
```

**6. Analyze.** The verdict thresholds come from the lock. The report labels any command-line override.

```bash
make experiment-analyze RUNS="runs/rules-trial/first" STORE=stores/rules-trial ANALYSIS_OUT=analysis/rules-trial
```

**7. Review.** Project the Quality element from the runs and the store, then serve the surface with the same runs. Scenario-epoch history under `experiments/benchmarks` is included when present.

```bash
make review-surface-quality-data QUALITY_RUNS=runs/rules-trial QUALITY_STORE=stores/rules-trial
make review-surface-serve REVIEW_RUNS_ROOT=runs/rules-trial REVIEW_STORE=stores/rules-trial
```

## Smoke checks

The smoke targets run real agent trials of `examples/smoke/` through the same experiment path. Each target locks its experiment under `.tmp/smoke/` with its own overrides and writes runs under `runs/`.

```bash
make orchestrator-smoke                                   # codex-cli + gpt-6-luna (low), one trial
make agent-smoke HARNESS=claude-code PROVIDER=anthropic MODEL=claude-sonnet-4-6
make smoke-matrix SMOKE_MATRIX_SELECTOR=codex             # one arm per pack harness: codex or claude
```

## Repository layout

- `orchestrator/`: the `raidar` CLI and library (`spec`, `problems`, `packs`, `components`, `execution`, `oracle`, `scorers`, `jev`, `analysis`, `review_projection`, `review_quality`).
- `examples/`: the Phase 1 pilot experiments and the smoke experiments.
- `problems/`: the pinned problem registry (tracked), plus the imported store and source cache (local).
- `review-surface/`: the review UI, its frozen projection contract (`schema_version=1`) and its Playwright suite.
- `runs/`: local run directories, ignored by git.
- `experiments/`, `scenarios/`, `matrices/`, `environments/`: scenario-epoch history, read-only.
- `docs/reboot/`: the requirements, plan, ADR, overhead baseline, design memo and the Phase 1 gate packet.

`make quality` is the repository gate: ruff, import contracts, lizard, mypy, fan-out, and the full test suite with coverage.
