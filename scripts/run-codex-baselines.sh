#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCH_DIR="$ROOT_DIR/orchestrator"
SCENARIO_PATH="../scenarios/homepage-implementation/v001/scenario.yaml"
AGENT="codex-cli"
REPEATS="${REPEATS:-5}"
REPEAT_PARALLEL="${REPEAT_PARALLEL:-1}"
RETRY_VOID="${RETRY_VOID:-1}"
TIMEOUT_SEC="${TIMEOUT_SEC:-300}"

if [[ -f "$ORCH_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ORCH_DIR/.env"
  set +a
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "Missing API key. Set OPENAI_API_KEY (or place it in orchestrator/.env)."
  exit 1
fi

cd "$ORCH_DIR"
uv run raidar harbor cleanup
uv run raidar agent validate --agent "$AGENT" --model "codex/gpt-5.4-high"

models=(
  "codex/gpt-5.2-low"
  "codex/gpt-5.2-medium"
  "codex/gpt-5.2-high"
  "codex/gpt-5.4-low"
  "codex/gpt-5.4-medium"
  "codex/gpt-5.4-high"
  "codex/gpt-5.4-extra-high"
)

for model in "${models[@]}"; do
  echo
  echo "Running baseline for $model (repeats=$REPEATS, parallel=$REPEAT_PARALLEL, retry_void=$RETRY_VOID, timeout=${TIMEOUT_SEC}s)"
  uv run raidar experiment run \
    --scenario "$SCENARIO_PATH" \
    --agent "$AGENT" \
    --model "$model" \
    --timeout "$TIMEOUT_SEC" \
    --repeats "$REPEATS" \
    --repeat-parallel "$REPEAT_PARALLEL" \
    --retry-void "$RETRY_VOID"
done

echo
echo "Completed codex baselines."
