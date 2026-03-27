<div align="center">

<h1>Raidar</h1>

**Scenario evaluation of CLI harness + model pairs (`AgentSpec`s) to improve delivery performance using Harbor-based runs**

![Status](https://img.shields.io/badge/status-active-brightgreen.svg?style=flat-square)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg?style=flat-square)
![Runtime](https://img.shields.io/badge/runtime-uv%20%7C%20docker-lightgrey.svg?style=flat-square)
![Primary CLI](https://img.shields.io/badge/cli-raidar-orange.svg?style=flat-square)

</div>

## Quick Install

Prerequisites:

- `uv`
- Docker with `docker compose`
- at least one harness/provider API key in `orchestrator/.env`

Bootstrap the environment:

```bash
cp orchestrator/.env.example orchestrator/.env
make env-setup
```

Use `make help` from the repo root for the supported command surface and target descriptions.

## Quick Start

Run one review-grade experiment for one `AgentSpec` (`harness + model`).

```bash
make harness-validate HARNESS=codex-cli MODEL=codex/gpt-5.4-mini
make experiment-run \
  SCENARIO=scenarios/hello-world-smoke/v001/scenario.yaml \
  HARNESS=codex-cli \
  MODEL=codex/gpt-5.4-mini \
  RUN_COUNT=5 \
  RUN_PARALLELISM=1 \
  RERUN_UNSCORED=0
```

This writes canonical artifacts into `experiments/`, including per-run `run.json`, experiment-level `experiment.json`, `experiment-summary.json`, and `report.md`.

Use `make orchestrator-smoke` when you want a fast orchestrator smoke/debug pass for one `AgentSpec`.

```bash
make orchestrator-smoke
```

Run a structured provider comparison with the public make surface:

```bash
make matrix-run scenarios/homepage-implementation/v001/scenario.yaml codex
```

Under the hood, that generates a matrix config using the public schema:

```yaml
matrix:
  experiment:
    timeout_sec: 1800
    repeats: 5
    repeat_parallel: 1
    retry_void: 1
  agents:
    - harness: codex-cli
      model: codex/gpt-5.2-high
    - harness: codex-cli
      model: codex/gpt-5.2-low
    - harness: codex-cli
      model: codex/gpt-5.2-medium
    - harness: codex-cli
      model: codex/gpt-5.4-extra-high
    - harness: codex-cli
      model: codex/gpt-5.4-high
    - harness: codex-cli
      model: codex/gpt-5.4-mini
    - harness: codex-cli
      model: codex/gpt-5.4-medium
```

## What Raidar Does

The repository has four primary concerns:

- `orchestrator/`: CLI and runtime pipeline that executes and scores scenarios.
- `scenarios/`: versioned scenario definitions (`scenario.yaml`), prompts, rules, references, and starters.
- `auto_researcher/`: objective-led workflow for scenario design and benchmark-driven research loops.
- `experiments/`: generated experiment artifacts with per-run evidence bundles.
- `experiments/` uses canonical experiment kinds:
  - `experiments/benchmarks/` for benchmark baselines
  - `experiments/research_loops/` for bounded research-loop batches

Raidar is built to answer one practical question: how well does a given harness and model perform against delivery scenarios that look like real project work. It helps you compare execution quality, reliability, and efficiency against the same scenario contract instead of relying on anecdotal impressions. The `auto_researcher` capability is currently beta and extends that workflow with objective-led scenario iteration in partnership with an LLM. It is designed around `codex-cli` and draws on ideas from Karpathy's [autoresearch](https://github.com/karpathy/autoresearch).

## Raidar Modes

Raidar supports two modes of use. Benchmark experiments analyze the same scenario across `AgentSpec` pairs so you can compare CLI harness and model combinations on shared evidence. Research loop experiments automate iterative work on a single scenario in partnership with an LLM so you can improve the scenario, benchmark, and supporting evidence over time.

### Benchmark Experiment

1. Define a scenario contract in `scenarios/.../scenario.yaml` plus prompt, rules, starter, and optional visual reference.
2. Validate the `AgentSpec` and the scenario contract before running.
3. Run one experiment for one `AgentSpec`, or use `make matrix-run <scenario-yaml> <all|codex|gemini|claude>` when you want a structured comparison across benchmark model sets.
4. Review artifacts in `experiments/`, especially `run.json`, `experiment-summary.json`, and `report.md`.
5. Use the evidence to improve prompts, rules, starter quality, scenario design, or the `AgentSpec` choice.

#### Example Commands

```bash
make scenario-validate SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml
make harness-validate HARNESS=codex-cli MODEL=codex/gpt-5.4-mini
make experiment-run SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml HARNESS=codex-cli MODEL=codex/gpt-5.4-mini
make matrix-run scenarios/homepage-implementation/v001/scenario.yaml codex
```

#### Questions This Helps Answer

- Which `AgentSpec` produces the most reliable result for a given scenario?
- Where is a result failing: functional correctness, acceptance, verification stability, execution validity, visual quality, or efficiency?
- Does a harness satisfy the stated scenario requirements and back them with tests?
- Does a visually sensitive scenario stay close to the intended reference design?
- Are repeated runs stable enough to trust for ranking and decision-making?

### Research Loop Experiment

1. Define an objective for a target harness and model, then draft a scenario around that goal.
2. Approve the drafted scenario and seed the initial benchmark in `experiments/benchmarks/`.
3. Run bounded research loops stored in `experiments/research_loops/`.
4. Review objective progress, current benchmark state, loop outputs, and reports.
5. Use the evidence to refine the scenario, objective framing, and benchmark promotion decisions before the next loop.

#### Example Commands

```bash
make auto-research-init GOAL='Improve homepage implementation benchmark reliability' TARGET_HARNESS=codex-cli TARGET_MODEL=codex/gpt-5.4-mini
make auto-research-approve-scenario OBJECTIVE_ID=homepage-reliability
make auto-research-run OBJECTIVE_ID=homepage-reliability
make auto-research-status OBJECTIVE_ID=homepage-reliability
make auto-research-report OBJECTIVE_ID=homepage-reliability
```

#### Questions This Helps Answer

- What changes to the scenario contract produce a stronger benchmark for the target harness and model?
- Is the objective converging, or are loops exposing unresolved gaps in the scenario design?
- Which scenario edits are improving verification clarity, acceptance coverage, or result quality over time?
- When should the current best loop output be promoted into the next benchmark baseline?

## Core Concepts

- A `scenario` is the contract: prompt, rules, starter, verification settings, acceptance requirements, metrics, and optional visual baseline.
- A `harness` is the executable/runtime surface previously referred to as an agent.
- An `AgentSpec` is one harness plus one model.
- An `experiment` is one `AgentSpec` run against one scenario, usually with repeats.
- A `benchmark` is the pinned baseline experiment used as the current comparison anchor for an autoresearch objective.
- A `research loop` is a bounded, iterative experiment batch run to improve benchmark-facing evidence for that objective.
- An `objective` is the optimization target in `auto_researcher` (goal, target harness/model, and control settings).
- A `matrix config` uses top-level `experiment` and `agents` blocks; each entry in `agents` must declare a `harness` and `model`.
- A `run artifact` is the evidence bundle for one repeat, centered on `run.json` plus verifier outputs and harness logs.
- An `evaluation_profile` is the ordered metric capability set derived from `metrics[]`; use it as part of the identity when comparing experiments.

Canonical artifact paths use `runs/` and `harness/` under each experiment.

## Go Deeper

- [docs/references/metrics.md](/Users/adamjackson/Projects/raidar/docs/references/metrics.md): what each metric measures, when to use it, and where to inspect evidence.
- [docs/references/homepage-scenario-walkthrough.md](/Users/adamjackson/Projects/raidar/docs/references/homepage-scenario-walkthrough.md): a high-level teaching walkthrough of the homepage scenario and eval design flow.
- [docs/references/raidar-framework-comparison.md](/Users/adamjackson/Projects/raidar/docs/references/raidar-framework-comparison.md): comparison memo covering RAIDAR's delivery-focused differentiators and how it compares with Inspect AI, Promptfoo, and DeepEval.
- [docs/references/auto-researcher.md](/Users/adamjackson/Projects/raidar/docs/references/auto-researcher.md): objective-led flow for benchmarks, research loops, and status/report outputs.
