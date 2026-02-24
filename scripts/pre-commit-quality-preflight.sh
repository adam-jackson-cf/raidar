#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is required. Install it before committing."
  exit 1
fi

if ! git diff --quiet; then
  echo "Error: Unstaged tracked changes detected."
  echo "Run: git add -u (or stash) before commit."
  exit 1
fi

if [ ! -f orchestrator/uv.lock ]; then
  echo "Error: orchestrator/uv.lock is missing."
  exit 1
fi
