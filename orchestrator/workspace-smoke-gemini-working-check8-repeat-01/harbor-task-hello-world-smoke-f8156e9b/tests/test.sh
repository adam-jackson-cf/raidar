#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p /logs/verifier /logs/agent
if [[ ! -d /app ]]; then
  echo "Missing /app workspace" >&2
  echo "0" > /logs/verifier/reward.txt
  exit 1
fi

if ! bun run "$SCRIPT_DIR/score-task.mjs" "$SCRIPT_DIR/task-spec.json"; then
  echo "0" > /logs/verifier/reward.txt
fi

tar   --exclude='./node_modules'   --exclude='./.next'   --exclude='./jobs'   --exclude='./actual.png'   --exclude='./diff.png'   -czf /logs/agent/final-app.tar.gz   -C /app .
