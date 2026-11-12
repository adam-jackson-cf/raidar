<div align="center">

<h1>Raidar</h1>

**Controlled experiments on coding agents: measure what an agent and workflow deliver on the same pinned problems.**

![Status](https://img.shields.io/badge/status-active-brightgreen.svg?style=flat-square)
![Host Python](https://img.shields.io/badge/host_python-3.13.7-blue.svg?style=flat-square)
![Runtime](https://img.shields.io/badge/runtime-uv%20%7C%20docker%20%7C%20harbor-lightgrey.svg?style=flat-square)
![Primary CLI](https://img.shields.io/badge/cli-raidar-orange.svg?style=flat-square)

</div>

## Quick Install

Run commands from the repository root. Prerequisites:

- Host Python **3.13.7** and host `uv >=0.10.0` for bootstrap only.
  Result-affecting calls use the exact uv dependency in `orchestrator/uv.lock`.
- Docker with `docker compose` **2.40.1 or newer**, for execution and grading.
- Node **18 or newer**, for the review surface and maintenance formatter.

```bash
env -u AGENT uv sync --project orchestrator --python 3.13.7 --all-extras --frozen
env -u AGENT make env-setup
npm ci --ignore-scripts
```

The host interpreter is pinned in `.python-version`; package compatibility remains
`requires-python = ">=3.12"`. Validated grading requires the exact pinned host
interpreter, not merely a compatible Python version.

Set up a Docker target and provider credentials:

- **Target:** run `env -u AGENT make target-init`, edit `~/.config/raidar/targets.toml`,
  then run `env -u AGENT make target-up TARGET=<name>` and
  `env -u AGENT make target-status TARGET=<name>`.
- **Selection:** `TARGET` → `RAIDAR_TARGET` → file default; the current Docker context is not a fallback.
- **Trust:** set `trusted = true` only for a target approved to receive agent credentials and hidden tests.
- **Secrets:** `SECRETS=auto|infisical|dotenv`; auto tries accessible Infisical, `orchestrator/.env`, then shell/file login. Infisical project `mac-os-cf`, environment `dev`, is bound by `orchestrator/.infisical.json`; use `orchestrator/.env.example` for dotenv.
- **Codex:** file-backed login (`env -u AGENT make codex-auth-setup`) or `OPENAI_API_KEY`; OS-keyring-only login cannot enter Harbor.
- **Claude Code:** `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or `CLAUDE_CODE_OAUTH_TOKEN`.

Use `env -u AGENT make help` for the exhaustive public command catalogue.

## Quick Start

First, inspect the shipped one-trial smoke without calling a model:

```bash
E=lab/experiments/smoke/python__circuit_eval-cp1__agent-health/experiment.yaml
env -u AGENT make experiment-validate EXPERIMENT="$E"
env -u AGENT make experiment-plan EXPERIMENT="$E"
```

The plan lists the selected trial and its hashes; it does not execute the agent.
This smoke binds `circuit_eval.checkpoint_1`, Codex `gpt-6-luna` at low reasoning,
and the one-shot workflow.

With credentials and a ready Docker target, the operational first run is:

```bash
env -u AGENT make orchestrator-smoke
```

This calls a real model and incurs provider usage. It writes a smoke lock under
`var/tmp/smoke/` and a job under `var/jobs/`; a smoke proves mechanics, not an
improvement in agent quality. For a controlled comparison, continue with the
[experiment workflow](docs/references/experiment-workflow.md).

## What Raidar Does

Raidar compares agents and workflows on pinned problems with repeated,
counterbalanced attempts. [Harbor](https://pypi.org/project/harbor/) runs trials
in Docker; a hidden host-only verifier grades each checkpoint. Offline scorers
produce Assessments, then analysis reports paired effects against a baseline.
The review surface exposes trial evidence and Quality components.

- `orchestrator/`: CLI, experiment design, execution, grading, scoring and analysis.
- `lab/`: tracked experiments, problem pins, releases, stage packages and ledger.
- `contract/`: portable package schemas and digests (`raidar-contract`).
- `review-surface/`: review UI and projection contract.
- `var/`: generated jobs, stores, analysis and caches; git-ignored.

[`split-design.md`](split-design.md) is the binding design. Python is the only
implemented runtime. Isolated sessions, opaque arms and non-Python runtimes are
not current execution alternatives.

## Core Concepts

Raidar uses Harbor vocabulary wherever Harbor supplies a term.

- **Experiment**: an authored comparison of problems, agents, arms and an analysis
  plan. Kinds are `ab-test`, `benchmark` and operational `smoke`;
  `research-loop` is reserved. The kind is part of the experiment lock.
- **Problem and checkpoint**: a pinned task and one graded step of that task.
  Multi-checkpoint trials retain the workspace between checkpoints.
- **Agent**: a pinned harness/adapter plus a model and its configuration.
  An **agent release** records its tool pin and supported runtimes.
- **Workflow**: a build prompt plus released **stage packages**, all contributing
  to one **primary session** per checkpoint. A package supplies prompts, tools or
  checks; Raidar-owned **primitives** supply execution guarantees.
- **Arm**: the system under test; today, one agent plus one workflow.
  An omitted workflow selects `raidar/one-shot@2`, with no stages.
- **Lock**: pinned content and versions. Its `lock_sha256` covers the execution
  section; jobs record that digest. Assessment and analysis settings are separately pinned.
- **Attempt**: a replicate that runs every arm once per problem, in seeded,
  counterbalanced order.
- **Job**: one execution of a locked experiment. A **trial** is one arm × attempt
  × problem; a **step result** is one checkpoint of that trial.
- **Verifier and reward**: the hidden trusted grader and its per-step output.
  Harbor's `oracle` agent instead replays reference solutions.
- **Scorer and Assessment**: an offline measurement implementation and its stored
  output. A **headline metric** combines selected scores; unavailable evidence is
  not silently zero.
- **Analysis and verdict**: paired comparisons against the baseline arm, with
  uncertainty and exclusions; benchmarks also add rankings. A workflow judge's
  `verified` ending is not a hidden-verifier correctness verdict.

## Go Deeper

- [Experiment workflow](docs/references/experiment-workflow.md): design, lock,
  execute, assess, analyze and review a comparison.
- [Problem onboarding](docs/references/problem-onboarding.md): import pins,
  solvable sets, declared dependencies and grading records.
- [Workflows and stages](docs/references/workflows-and-stages.md): author packages,
  understand sessions, gate seals and judge evidence.
- [Scoring](docs/references/scoring.md): select measurements and
  interpret missing evidence.
- [Health and pooling](docs/references/health-and-pooling.md): diagnose validity,
  compare jobs and interpret overhead.
- [Contributing](docs/references/contributing.md): pass
  contributor gates and release pinned artifacts.
- [Stage authoring contract](lab/stages/README.md) and
  [runtime-pack contract](lab/environments/README.md): the public extension boundaries.
