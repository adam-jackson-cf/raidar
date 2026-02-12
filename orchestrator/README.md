# Eval Orchestrator

Agentic evaluation system orchestrator for testing model/harness combinations on coding tasks.

## Prerequisites

- [uv](https://github.com/astral-sh/uv) for Python dependency management
- Docker Desktop (or another Docker runtime) so Harbor can launch Terminal-Bench containers
- Docker Compose `>= 2.40.1` (required for stable Harbor environment builds)
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
  --retry-void 1
```

For baseline generation, run suites sequentially per model and sequentially per repeat:

```bash
REPEATS=5 REPEAT_PARALLEL=1 RETRY_VOID=1 TIMEOUT_SEC=300 ./scripts/run-codex-baselines.sh
```

Provider smoke run (single end-to-end Harbor integration check):

```bash
./scripts/run-provider-smoke.sh --agent gemini --model google/gemini-3-flash-preview
./scripts/run-provider-smoke.sh --agent claude-code --model anthropic/claude-haiku-4-5
./scripts/run-provider-smoke.sh --agent codex-cli --model codex/gpt-5.2-high
```

The smoke runner uses `tasks/hello-world-smoke/task.yaml`, which is intentionally lightweight and suitable for validating harness/provider wiring before running heavier benchmark tasks.

Use `--fast` to enable custom no-setup Harbor agents plus prebuilt image reuse:

```bash
./scripts/run-provider-smoke.sh --fast --agent gemini --model google/gemini-3-flash-preview
```

`run-provider-smoke.sh` defaults to `--rules none` for quick integration checks.

Void-result policy:

- Runs are marked `voided=true` when Harbor/harness/provider issues are detected (for example Harbor timeout, provider rate limit, stream disconnect, or Harbor runtime failure).
- Voided runs are flagged as `repeat_required` and excluded from scored aggregate statistics.
- `--retry-void` accepts `0` or `1`; when set to `1`, each voided run gets at most one retry attempt.
- Suite summaries include `void_count`, `repeat_required_count`, retry usage, and whether target scored runs were reached.

> **Note:** Harbor requires Docker to be running; if it is missing, the orchestrator will terminate early with `Harbor not installed`. The orchestrator also hard-fails preflight when Docker Compose is below `2.40.1`.

## Secrets via `.env`

The CLI automatically loads environment variables from `orchestrator/.env` (if present) before executing any commands. Start by copying `orchestrator/.env.example` to `orchestrator/.env` (it is ignored by git), then fill only the credentials you need:

```
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
CLAUDE_CODE_API_KEY=
GEMINI_API_KEY=
COPILOT_API_KEY=
```

Only populate the entries you use. Values in your shell environment still take precedence if you need to override something temporarily.

For Harbor `codex` agent runs, an API key is required in-container via `OPENAI_API_KEY`.
For Harbor `claude-code` agent runs, the orchestrator accepts either `ANTHROPIC_API_KEY` or `CLAUDE_CODE_API_KEY` and forwards it as `ANTHROPIC_API_KEY`.
For Harbor `gemini` agent runs with `google/*` models, provide `GEMINI_API_KEY`, with supported model ids:
- `google/gemini-3-pro-preview`
- `google/gemini-3-flash-preview`

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
