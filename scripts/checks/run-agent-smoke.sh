#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

SCENARIO_PATH="scenarios/hello-world-smoke/v001/scenario.yaml"
TIMEOUT_SEC="300"
REPEATS="1"
REPEAT_PARALLEL="1"
RERUN_UNSCORED="0"
FAST_MODE="0"
HARNESS=""
MODEL=""
MAKE_ARGS=(-C "$ROOT_DIR")

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

if [[ "$FAST_MODE" == "1" ]]; then
  export HARBOR_SMOKE_FAST=1
  export HARBOR_SMOKE_FAST_REUSE_IMAGE=1
fi

make "${MAKE_ARGS[@]}" agent-smoke \
  HARNESS="$HARNESS" \
  MODEL="$MODEL" \
  TIMEOUT_SEC="$TIMEOUT_SEC" \
  AGENT_SMOKE_SCENARIO="$SCENARIO_PATH" \
  AGENT_SMOKE_REPEATS="$REPEATS" \
  AGENT_SMOKE_REPEAT_PARALLEL="$REPEAT_PARALLEL" \
  AGENT_SMOKE_RERUN_UNSCORED="$RERUN_UNSCORED"
