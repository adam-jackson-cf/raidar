#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCH_DIR="${ROOT_DIR}/orchestrator"

cd "${ORCH_DIR}"
uv run eval-orchestrator env setup "$@"
