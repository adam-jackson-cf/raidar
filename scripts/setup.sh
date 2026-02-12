#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCH_DIR="${REPO_ROOT}/orchestrator"
MIN_DOCKER_COMPOSE_VERSION="2.40.1"

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is not installed. See https://github.com/astral-sh/uv for installation instructions." >&2
  exit 1
fi

echo "==> Cleaning stale Harbor resources"
"${REPO_ROOT}/scripts/cleanup-stale-harbor.sh" || true

if ! command -v docker >/dev/null 2>&1; then
  echo "Warning: Docker is not installed or not on PATH. Harbor requires Docker for local runs." >&2
else
  if ! compose_version_raw="$(docker compose version --short 2>/dev/null)"; then
    echo "Error: docker compose is unavailable. Harbor requires docker compose >= ${MIN_DOCKER_COMPOSE_VERSION}." >&2
    exit 1
  fi
  compose_version="${compose_version_raw#v}"
  compose_version="${compose_version%%-*}"
  if [[ -z "${compose_version}" ]]; then
    echo "Error: unable to parse docker compose version from '${compose_version_raw}'." >&2
    exit 1
  fi
  oldest="$(printf '%s\n%s\n' "${MIN_DOCKER_COMPOSE_VERSION}" "${compose_version}" | sort -V | head -n 1)"
  if [[ "${oldest}" != "${MIN_DOCKER_COMPOSE_VERSION}" ]]; then
    echo "Error: docker compose ${compose_version} is unsupported. Require >= ${MIN_DOCKER_COMPOSE_VERSION} for Harbor runs." >&2
    exit 1
  fi
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
