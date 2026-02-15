#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCH_DIR="$ROOT_DIR/orchestrator"

TASK_PATH="../tasks/hello-world-smoke/task.yaml"
SCAFFOLDS_ROOT="../scaffolds"
WORKSPACE="workspace-smoke"
OUTPUT="results-smoke"
RULES="none"
TIMEOUT_SEC="300"
REPEATS="1"
REPEAT_PARALLEL="1"
RETRY_VOID="0"
FAST_MODE="0"
AGENT=""
MODEL=""

usage() {
  cat <<'USAGE'
Usage: scripts/run-provider-smoke.sh --agent <agent> --model <provider/model> [options]

Required:
  --agent            Harness agent (codex-cli|claude-code|gemini)
  --model            Model id (e.g. codex/gpt-5.2-high)

Optional:
  --rules            Rules variant (strict|minimal|none), default: none
  --timeout          Timeout in seconds, default: 300
  --workspace        Workspace base dir (relative to orchestrator/), default: workspace-smoke
  --output           Output base dir (relative to orchestrator/), default: results-smoke
  --repeats          Repeat count, default: 1
  --repeat-parallel  Repeat parallelism, default: 1
  --retry-void       Void retry budget (0|1), default: 0
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
    --agent)
      AGENT="$2"
      shift 2
      ;;
    --model)
      MODEL="$2"
      shift 2
      ;;
    --rules)
      RULES="$2"
      shift 2
      ;;
    --timeout)
      TIMEOUT_SEC="$2"
      shift 2
      ;;
    --workspace)
      WORKSPACE="$2"
      shift 2
      ;;
    --output)
      OUTPUT="$2"
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
    --retry-void)
      RETRY_VOID="$2"
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

if [[ -z "$AGENT" || -z "$MODEL" ]]; then
  usage
  exit 1
fi

if [[ "$AGENT" != "codex-cli" && "$AGENT" != "claude-code" && "$AGENT" != "gemini" ]]; then
  echo "Unsupported --agent '$AGENT'. Expected one of: codex-cli, claude-code, gemini" >&2
  exit 1
fi

if [[ "$RULES" != "strict" && "$RULES" != "minimal" && "$RULES" != "none" ]]; then
  echo "Unsupported --rules '$RULES'. Expected one of: strict, minimal, none" >&2
  exit 1
fi

if [[ -f "$ORCH_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ORCH_DIR/.env"
  set +a
fi

case "$AGENT" in
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
uv run eval-orchestrator harbor cleanup

uv run eval-orchestrator provider validate \
  --agent "$AGENT" \
  --model "$MODEL" \
  --rules "$RULES" \
  --timeout "$TIMEOUT_SEC"

uv run eval-orchestrator run \
  --task "$TASK_PATH" \
  --agent "$AGENT" \
  --model "$MODEL" \
  --rules "$RULES" \
  --scaffolds-root "$SCAFFOLDS_ROOT" \
  --workspace "$WORKSPACE" \
  --output "$OUTPUT" \
  --timeout "$TIMEOUT_SEC" \
  --repeats "$REPEATS" \
  --repeat-parallel "$REPEAT_PARALLEL" \
  --retry-void "$RETRY_VOID"
