#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
}

require_cmd uv

uv tool install pre-commit
cd "$ROOT_DIR"
pre-commit install

echo "Pre-commit hook installed."
