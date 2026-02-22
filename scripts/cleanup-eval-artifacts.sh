#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEEP_PER_MODEL="${KEEP_PER_MODEL:-1}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_DIR="${ARCHIVE_DIR:-/tmp/raidar-archive/$STAMP}"
EXECUTIONS_DIR="$ROOT_DIR/executions"

mkdir -p "$ARCHIVE_DIR"

move_if_exists() {
  local src="$1"
  local rel
  rel="${src#$ROOT_DIR/}"
  if [[ -e "$src" ]]; then
    mkdir -p "$ARCHIVE_DIR/$(dirname "$rel")"
    mv "$src" "$ARCHIVE_DIR/$rel"
    echo "archived: $rel"
  fi
}

execution_model_key() {
  local execution_dir="$1"
  python3 - "$execution_dir" <<'PY'
import json
import sys
from pathlib import Path

execution_dir = Path(sys.argv[1])
candidates = (
    execution_dir / "suite-summary.json",
    execution_dir / "suite.json",
    execution_dir / "runs" / "run-01" / "run.json",
)

for candidate in candidates:
    if not candidate.is_file():
        continue
    try:
        payload = json.loads(candidate.read_text())
    except Exception:
        continue
    config = payload.get("config")
    if not isinstance(config, dict):
        continue
    model = config.get("model")
    if isinstance(model, str) and model:
        print(model.replace("/", "__"))
        raise SystemExit(0)

print("unknown-model")
PY
}

# Archive legacy split roots and stale workspaces.
move_if_exists "$ROOT_DIR/jobs"
move_if_exists "$ROOT_DIR/orchestrator/jobs"
move_if_exists "$ROOT_DIR/orchestrator/results"
move_if_exists "$ROOT_DIR/orchestrator/executions"
move_if_exists "$ROOT_DIR/orchestrator/executions-smoke"
move_if_exists "$ROOT_DIR/orchestrator/executions-baseline"
for workspace_variant in "$ROOT_DIR"/orchestrator/workspace*; do
  if [[ -e "$workspace_variant" ]]; then
    move_if_exists "$workspace_variant"
  fi
done

if [[ -d "$EXECUTIONS_DIR" ]]; then
  KEPT_FILE="$(mktemp)"
  : > "$KEPT_FILE"

  get_count() {
    local key="$1"
    local value
    value="$(awk -F ',' -v k="$key" '$1 == k { print $2 }' "$KEPT_FILE" | tail -n 1)"
    if [[ -z "$value" ]]; then
      echo "0"
      return
    fi
    echo "$value"
  }

  set_count() {
    local key="$1"
    local next="$2"
    awk -F ',' -v k="$key" '$1 != k { print $0 }' "$KEPT_FILE" > "$KEPT_FILE.tmp"
    printf '%s,%s\n' "$key" "$next" >> "$KEPT_FILE.tmp"
    mv "$KEPT_FILE.tmp" "$KEPT_FILE"
  }

  while IFS= read -r execution_dir; do
    model_slug="$(execution_model_key "$execution_dir")"
    count="$(get_count "$model_slug")"
    if (( count < KEEP_PER_MODEL )); then
      set_count "$model_slug" "$((count + 1))"
      continue
    fi
    rel="${execution_dir#$ROOT_DIR/}"
    mkdir -p "$ARCHIVE_DIR/$(dirname "$rel")"
    mv "$execution_dir" "$ARCHIVE_DIR/$rel"
    echo "pruned: $rel"
  done < <(find "$EXECUTIONS_DIR" -mindepth 1 -maxdepth 1 -type d -print | sort -r)
fi

echo "archive_dir=$ARCHIVE_DIR"
