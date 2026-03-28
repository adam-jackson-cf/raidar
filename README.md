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

Run one repeatable experiment for one `AgentSpec` (`harness + model`).

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

Artifacts land in `experiments/`, including per-run `run.json`, experiment-level `experiment.json`, `experiment-summary.json`, and `report.md`.

Use the public make surface for faster smoke runs or structured provider-family comparisons:

```bash
make orchestrator-smoke
make matrix-run scenarios/homepage-implementation/v001/scenario.yaml codex
```

## What Raidar Does

The repository has four primary concerns:

- `orchestrator/`: CLI and runtime pipeline that executes and scores scenarios.
- `scenarios/`: versioned scenario definitions (`scenario.yaml`), prompts, rules, references, and starters.
- `experiments/`: generated experiment artifacts with per-run evidence bundles.
- `experiments/benchmarks/`: canonical artifact root for comparison baselines.

Raidar answers a practical question: how well does a given harness and model perform against delivery scenarios that look like real project work. It lets you compare execution quality, reliability, and efficiency against the same scenario contract instead of relying on anecdotal impressions.

## Raidar Modes

Raidar's stable public workflow is benchmarking `AgentSpecs` (`harness + model` pairs) against shared scenario contracts.

### Benchmark Experiment

Use benchmark experiments when you want a comparison baseline for one scenario. They help you rank `AgentSpec` choices, inspect where failures cluster, and help you understand whats best for your delivery scenario.

> "Whats the best AgentSpec for implementing this design in a project context that matches my own"

#### Example Commands

```bash
make scenario-validate SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml
make harness-validate HARNESS=codex-cli MODEL=codex/gpt-5.4-mini
make experiment-run SCENARIO=scenarios/homepage-implementation/v001/scenario.yaml HARNESS=codex-cli MODEL=codex/gpt-5.4-mini
make matrix-run scenarios/homepage-implementation/v001/scenario.yaml codex
```

## Core Concepts

- A `scenario` is the contract: prompt, rules, starter, verification settings, acceptance requirements, metrics, and optional visual baseline.
- A `harness` is the executable/runtime surface previously referred to as an agent.
- An `AgentSpec` is one harness plus one model.
- An `experiment` is one `AgentSpec` run against one scenario, usually with repeats.
- A `benchmark` is a pinned experiment used as a stable comparison anchor across runs, scenario revisions, or decision points.
- A `matrix config` uses top-level `experiment` and `agents` blocks; each entry in `agents` must declare a `harness` and `model`.
- A `run artifact` is the evidence bundle for one repeat, centered on `run.json` plus verifier outputs and harness logs.
- An `evaluation_profile` is the ordered metric capability set derived from `metrics[]`; use it as part of the identity when comparing experiments.

Canonical artifact paths use `runs/` and `harness/` under each experiment.

## Go Deeper

- [docs/references/metrics.md](/Users/adamjackson/Projects/raidar/docs/references/metrics.md): what each metric measures, when to use it, and where to inspect evidence.
- [docs/references/homepage-scenario-walkthrough.md](/Users/adamjackson/Projects/raidar/docs/references/homepage-scenario-walkthrough.md): a high-level teaching walkthrough of the homepage scenario and eval design flow.
- [docs/references/raidar-framework-comparison.md](/Users/adamjackson/Projects/raidar/docs/references/raidar-framework-comparison.md): comparison memo covering RAIDAR's delivery-focused differentiators and how it compares with Inspect AI, Promptfoo, and DeepEval.
