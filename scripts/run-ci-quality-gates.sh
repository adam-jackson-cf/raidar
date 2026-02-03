#!/usr/bin/env bash
# Centralized quality gates for local pre-commit and CI.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCH_DIR="${ROOT_DIR}/orchestrator"

MODE="check"
AUTO_STAGE=false

usage() {
    cat <<'USAGE'
Usage: scripts/run-ci-quality-gates.sh [--fix] [--stage]

Options:
  --fix     Apply auto-fixes where supported (ruff format, ruff check --fix).
  --stage   Stage tracked file changes after fixes (requires git).
  --help    Show this help text.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fix)
            MODE="fix"
            ;;
        --stage)
            AUTO_STAGE=true
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
    shift
done

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
}

if [[ ! -d "$ORCH_DIR" ]]; then
    echo "Orchestrator directory not found: $ORCH_DIR" >&2
    exit 1
fi

require_cmd uv
require_cmd git
require_cmd python3.12

PYTHON_BIN="$(command -v python3.12)"

uv_project_run() {
    uv run --project "$ORCH_DIR" --python "$PYTHON_BIN" --extra dev "$@"
}

if [[ "$MODE" == "fix" ]]; then
    if ! git -C "$ROOT_DIR" diff --quiet; then
        echo "[quality-gates] Unstaged changes detected. Stage or stash before running --fix." >&2
        exit 1
    fi
fi

cd "$ORCH_DIR"

if [[ "$MODE" == "fix" ]]; then
    echo "[quality-gates] Ruff format (fix)"
    uv_project_run ruff format --force-exclude
    echo "[quality-gates] Ruff lint (fix)"
    uv_project_run ruff check . --fix --force-exclude
else
    echo "[quality-gates] Ruff format (check)"
    uv_project_run ruff format --check --force-exclude
    echo "[quality-gates] Ruff lint (check)"
    uv_project_run ruff check . --no-fix --force-exclude
fi

echo "[quality-gates] Pytest"
uv_project_run pytest tests -x --tb=short

if [[ "$AUTO_STAGE" == "true" ]]; then
    echo "[quality-gates] Staging fixes"
    git -C "$ROOT_DIR" add -u
fi

echo "[quality-gates] Completed successfully"
