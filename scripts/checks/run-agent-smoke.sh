#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ORCH_DIR="$ROOT_DIR/orchestrator"

SCENARIO_PATH="../scenarios/hello-world-smoke/v001/scenario.yaml"
TIMEOUT_SEC="300"
REPEATS="1"
REPEAT_PARALLEL="1"
RERUN_UNSCORED="0"
FAST_MODE="0"
HARNESS=""
MODEL=""

usage() {
  cat <<'USAGE'
Usage: scripts/checks/run-agent-smoke.sh --harness <harness> [--model <model>] [options]

Required:
  --harness          Harness id (codex-cli|claude-code|gemini)

Optional:
  --model            Model id; defaults to codex/gpt-5.4-low for codex-cli
  --timeout          Timeout in seconds, default: 300
  --repeats          Repeat count, default: 1
  --repeat-parallel  Repeat parallelism, default: 1
  --rerun-unscored   Unscored rerun budget (0|1), default: 0
  --fast             Enable fast smoke mode (custom Harbor agents + prebuilt image reuse)
  --help             Show this help text
USAGE
}

require_env_present() {
  local key="$1"
  if [[ -z "${!key:-}" ]]; then
    echo "Missing required environment variable: $key" >&2
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --harness)
      HARNESS="$2"
      shift 2
      ;;
    --agent)
      echo "--agent is no longer supported; use --harness" >&2
      exit 1
      ;;
    --model)
      MODEL="$2"
      shift 2
      ;;
    --timeout)
      TIMEOUT_SEC="$2"
      shift 2
      ;;
    --repeats)
      REPEATS="$2"
      shift 2
      ;;
    --repeat-parallel)
      REPEAT_PARALLEL="$2"
      shift 2
      ;;
    --rerun-unscored)
      RERUN_UNSCORED="$2"
      shift 2
      ;;
    --fast)
      FAST_MODE="1"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$HARNESS" ]]; then
  usage
  exit 1
fi

if [[ "$HARNESS" != "codex-cli" && "$HARNESS" != "claude-code" && "$HARNESS" != "gemini" ]]; then
  echo "Unsupported --harness '$HARNESS'. Expected one of: codex-cli, claude-code, gemini" >&2
  exit 1
fi

if [[ -z "$MODEL" && "$HARNESS" == "codex-cli" ]]; then
  MODEL="codex/gpt-5.4-low"
fi

if [[ -z "$MODEL" ]]; then
  echo "Missing required --model for harness '$HARNESS'" >&2
  usage
  exit 1
fi

if [[ -f "$ORCH_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ORCH_DIR/.env"
  set +a
fi

case "$HARNESS" in
  codex-cli)
    require_env_present OPENAI_API_KEY
    ;;
  claude-code)
    if [[ -z "${ANTHROPIC_API_KEY:-}" && -z "${CLAUDE_CODE_API_KEY:-}" ]]; then
      echo "Missing required environment variable: ANTHROPIC_API_KEY or CLAUDE_CODE_API_KEY" >&2
      exit 1
    fi
    ;;
  gemini)
    require_env_present GEMINI_API_KEY
    ;;
esac

if [[ "$FAST_MODE" == "1" ]]; then
  export HARBOR_SMOKE_FAST=1
  export HARBOR_SMOKE_FAST_REUSE_IMAGE=1
fi

cd "$ORCH_DIR"
uv run raidar harbor cleanup

uv run raidar harness validate \
  --harness "$HARNESS" \
  --model "$MODEL" \
  --timeout "$TIMEOUT_SEC"

uv run raidar experiment run \
  --scenario "$SCENARIO_PATH" \
  --harness "$HARNESS" \
  --model "$MODEL" \
  --timeout "$TIMEOUT_SEC" \
  --repeats "$REPEATS" \
  --repeat-parallel "$REPEAT_PARALLEL" \
  --rerun-unscored "$RERUN_UNSCORED"
