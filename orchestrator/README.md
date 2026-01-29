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

## Secrets via `.env`

The CLI automatically loads environment variables from `orchestrator/.env` (if present) before executing any commands. Create this file locally (it is ignored by git) and define the agent-specific credentials you need, for example:

```
# orchestrator/.env
CODEX_API_KEY=sk-live-...
CODEX_OAUTH_TOKEN=
CURSOR_API_KEY=
COPILOT_API_KEY=
PI_API_TOKEN=
```

Only populate the entries you use. Values in your shell environment still take precedence if you need to override something temporarily.
