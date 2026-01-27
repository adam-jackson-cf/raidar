#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCH_DIR="${REPO_ROOT}/orchestrator"

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is not installed. See https://github.com/astral-sh/uv for installation instructions." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Warning: Docker is not installed or not on PATH. Harbor requires Docker for local runs." >&2
fi

echo "==> Ensuring Python toolchain via uv"
pushd "${ORCH_DIR}" >/dev/null
uv python install 3.12
uv sync "$@"
popd >/dev/null

echo "==> Installing Harbor CLI via uv tool install"
uv tool install harbor

echo "==> Harbor version"
harbor --version || true

echo "Setup complete. You can now run evals via 'uv run eval-orchestrator ...' from ${ORCH_DIR}."
