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

Repeat-suite run (5 repeats, parallel workers):

```bash
uv run eval-orchestrator run \
  --task ../tasks/homepage-implementation/task.yaml \
  --agent codex-cli \
  --model codex/gpt-5.2-low \
  --scaffolds-root ../scaffolds \
  --workspace workspace \
  --output results \
  --repeats 5 \
  --repeat-parallel 5 \
  --retry-void 3
```

For baseline generation, run suites sequentially per model and sequentially per repeat:

```bash
REPEATS=5 REPEAT_PARALLEL=1 RETRY_VOID=3 TIMEOUT_SEC=300 ./scripts/run-codex-baselines.sh
```

Void-result policy:

- Runs are marked `voided=true` when Harbor/harness/provider issues are detected (for example Harbor timeout, provider rate limit, stream disconnect, or Harbor runtime failure).
- Voided runs are flagged as `repeat_required` and excluded from scored aggregate statistics.
- `--retry-void N` reruns only the voided runs up to `N` retry rounds to recover target scored sample size.
- Suite summaries include `void_count`, `repeat_required_count`, retry usage, and whether target scored runs were reached.

> **Note:** Harbor requires Docker to be running; if it is missing, the orchestrator will terminate early with `Harbor not installed`.

## Secrets via `.env`

The CLI automatically loads environment variables from `orchestrator/.env` (if present) before executing any commands. Create this file locally (it is ignored by git) and define the agent-specific credentials you need, for example:

```
# orchestrator/.env
OPENAI_API_KEY=sk-live-...
CODEX_API_KEY=
CURSOR_API_KEY=
COPILOT_API_KEY=
PI_API_TOKEN=
```

Only populate the entries you use. Values in your shell environment still take precedence if you need to override something temporarily.

For Harbor `codex` agent runs, an API key is required in-container. The orchestrator accepts either `OPENAI_API_KEY` or `CODEX_API_KEY` and forwards it to Harbor as `OPENAI_API_KEY`.

Model aliases are supported for Codex tiers:
- `codex/gpt-5.2-low` -> `codex/gpt-5.2-codex` with `reasoning_effort=low`
- `codex/gpt-5.2-medium` -> `codex/gpt-5.2-codex` with `reasoning_effort=medium`
- `codex/gpt-5.2-high` -> `codex/gpt-5.2-codex` with `reasoning_effort=high`

## Artifact Layout

Each run writes a canonical instance folder at:

`orchestrator/results/runs/<instance_id>__<timestamp>__<task>__<agent>__<model>/`

Subdirectories:

- `summary/` -> scored `result.json` plus `README.md` pointers
- `agent/` -> agent trajectory and full interaction logs (`codex.txt`, `trajectory.json`)
- `verifier/` -> verifier scorecard and gate outputs
- `scaffold/` -> scaffold manifests and metadata snapshots
- `harbor/` -> copied Harbor job/trial logs used for quick inspection

Raw Harbor jobs are retained separately at:

`orchestrator/jobs/orchestrator-<run_id>/`

The summary README includes `raw_harbor_job_dir` and `raw_harbor_trial_dir` pointers so canonical outputs and raw job artifacts stay linked by run id.

Repeat suites are stored at:

`orchestrator/results/suites/<timestamp>__<task>__<agent>__<model>__x<repeats>/`

Suite artifacts include:

- `summary.json` -> aggregate statistics (mean/median/stddev) plus individual run pointers
- `README.md` -> human-readable suite summary

To archive stale artifacts and keep only the latest runs per model:

```bash
KEEP_PER_MODEL=1 ./scripts/cleanup-eval-artifacts.sh
```

When suite summaries are retained, the cleanup script preserves all run folders referenced by those suites so per-repeat artifacts remain reviewable.
