# Eval Orchestrator

Agentic evaluation system orchestrator for testing model/harness combinations on coding tasks.

## Prerequisites

- [uv](https://github.com/astral-sh/uv) for Python dependency management
- Docker Desktop (or another Docker runtime) so Harbor can launch Terminal-Bench containers
- Harbor CLI (installed automatically by the setup script)

## Setup

From the repo root:

```bash
./scripts/setup.sh
```

This script:

1. Ensures Python 3.12 is installed via `uv python install`
2. Runs `uv sync` inside `orchestrator/` to install all project dependencies
3. Installs the `harbor` CLI with `uv tool install harbor`
4. Prints the active Harbor version so you can confirm it is on your `PATH`

You can pass any extra arguments you’d normally forward to `uv sync` (e.g., `--no-install-project`) and they will propagate through the script.

## Usage

After setup, run orchestrator commands via uv:

```bash
cd orchestrator
uv run eval-orchestrator --help
```

Example single-task run:

```bash
uv run eval-orchestrator run \
  --task ../tasks/homepage-implementation/task.yaml \
  --agent codex-cli \
  --model codex/gpt-5.2-high \
  --scaffolds-root ../scaffolds \
  --workspace workspace \
  --output results
```

> **Note:** Harbor requires Docker to be running; if it is missing, the orchestrator will terminate early with `Harbor not installed`.
