# Orchestrator Usage

## Prerequisites

- `uv`
- Docker runtime
- Docker Compose `>= 2.40.1`
- Harbor CLI

## Setup

From repository root:

```bash
./scripts/setup.sh
```

## Common Commands

```bash
cd orchestrator
uv run eval-orchestrator --help
```

Single run:

```bash
uv run eval-orchestrator run \
  --task ../tasks/homepage-implementation/v001/task.yaml \
  --agent codex-cli \
  --model codex/gpt-5.2-high
```

Suite run:

```bash
uv run eval-orchestrator suite run \
  --task ../tasks/homepage-implementation/v001/task.yaml \
  --agent codex-cli \
  --model codex/gpt-5.2-low \
  --repeats 5 \
  --repeat-parallel 1 \
  --retry-void 1
```

Quality gates:

```bash
./scripts/run-ci-quality-gates.sh
```

Provider smoke:

```bash
./scripts/run-provider-smoke.sh --agent codex-cli --model codex/gpt-5.2-high
./scripts/run-provider-smoke.sh --agent claude-code --model anthropic/claude-haiku-4-5
./scripts/run-provider-smoke.sh --agent gemini --model google/gemini-3-flash-preview
```

Codex baselines:

```bash
REPEATS=5 REPEAT_PARALLEL=1 RETRY_VOID=1 TIMEOUT_SEC=300 ./scripts/run-codex-baselines.sh
```

## Secrets

Use `orchestrator/.env` (git-ignored), for example:

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
CLAUDE_CODE_API_KEY=
GEMINI_API_KEY=
COPILOT_API_KEY=
```

## Artifact Paths

Per suite:

`executions/<timestamp>__<task>__<version>/`

Contains:
- `suite.json`
- `suite-summary.json`
- `analysis.md`
- `workspace/baseline/`
- `runs/`
  - `run-XX/homepage-pre.png`
  - `run-XX/homepage-post.png`

## Cleanup

Keep latest execution per model:

```bash
KEEP_PER_MODEL=1 ./scripts/cleanup-eval-artifacts.sh
```

Archive all retained execution roots:

```bash
KEEP_PER_MODEL=0 ./scripts/cleanup-eval-artifacts.sh
```
