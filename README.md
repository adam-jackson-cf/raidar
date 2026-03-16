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
make harness-validate HARNESS=codex-cli MODEL=codex/gpt-5.4-low
make experiment-run \
  SCENARIO=scenarios/hello-world-smoke/v001/scenario.yaml \
  HARNESS=codex-cli \
  MODEL=codex/gpt-5.4-low \
  RUN_COUNT=5 \
  RUN_PARALLELISM=1 \
  RERUN_UNSCORED=0
```

This writes canonical artifacts into `experiments/`, including per-run `run.json`, experiment-level `experiment.json`, `experiment-summary.json`, and `report.md`.

Use `make smoke` when you want a fast smoke/debug pass for one `AgentSpec`.

```bash
make smoke
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
      model: codex/gpt-5.4-low
    - harness: codex-cli
      model: codex/gpt-5.4-medium
```

## What Raidar Does

The repository has three primary concerns:

- `orchestrator/`: CLI and runtime pipeline that executes and scores scenarios.
- `scenarios/`: versioned scenario definitions (`scenario.yaml`), prompts, rules, references, and starters.
- `experiments/`: generated experiment artifacts with per-run evidence bundles.

Raidar is built to answer one practical question: how well does a given harness and model perform against delivery scenarios that look like real project work. It helps you compare execution quality, reliability, and efficiency against the same scenario contract instead of relying on anecdotal impressions.

## Experiment Flow

1. Define a scenario contract in `scenarios/.../scenario.yaml` plus prompt, rules, starter, and optional visual reference.
2. Validate the harness/model pair and the scenario contract before running.
3. Run one experiment for one `AgentSpec`, or use `make matrix-run <scenario-yaml> <all|codex|gemini|claude>` when you want a structured comparison across benchmark model sets.
4. Review artifacts in `experiments/`, especially `run.json`, `experiment-summary.json`, and `report.md`.
5. Use the evidence to improve prompts, rules, starter quality, scenario design, or the `AgentSpec` choice.

## Questions This Helps Answer

- Which `AgentSpec` produces the most reliable result for a given scenario?
- Where is a result failing: functional correctness, acceptance, verification stability, execution validity, visual quality, or efficiency?
- Does a harness satisfy the stated scenario requirements and back them with tests?
- Does a visually sensitive scenario stay close to the intended reference design?
- Are repeated runs stable enough to trust for ranking and decision-making?

## Core Concepts

- A `scenario` is the contract: prompt, rules, starter, verification settings, acceptance requirements, metrics, and optional visual baseline.
- A `harness` is the executable/runtime surface previously referred to as an agent.
- An `AgentSpec` is one harness plus one model.
- An `experiment` is one `AgentSpec` run against one scenario, usually with repeats.
- A `matrix config` uses top-level `experiment` and `agents` blocks; each entry in `agents` must declare a `harness` and `model`.
- A `run artifact` is the evidence bundle for one repeat, centered on `run.json` plus verifier outputs and harness logs.
- An `evaluation_profile` is the ordered metric capability set derived from `metrics[]`; use it as part of the identity when comparing experiments.

Canonical artifact paths use `runs/` and `harness/` under each experiment.

## Go Deeper

- [docs/references/metrics.md](/Users/adamjackson/Projects/raidar/docs/references/metrics.md): what each metric measures, when to use it, and where to inspect evidence.
- [docs/references/homepage-scenario-walkthrough.md](/Users/adamjackson/Projects/raidar/docs/references/homepage-scenario-walkthrough.md): a high-level teaching walkthrough of the homepage scenario and eval design flow.
- [docs/references/raidar-framework-comparison.md](/Users/adamjackson/Projects/raidar/docs/references/raidar-framework-comparison.md): comparison memo covering RAIDAR's delivery-focused differentiators and how it compares with Inspect AI, Promptfoo, and DeepEval.
