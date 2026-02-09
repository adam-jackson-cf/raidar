#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEEP_PER_MODEL="${KEEP_PER_MODEL:-1}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_DIR="${ARCHIVE_DIR:-/tmp/typescript-ui-eval-archive/$STAMP}"

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

PRESERVED_RUN_IDS_FILE="$(mktemp)"
: > "$PRESERVED_RUN_IDS_FILE"

record_suite_run_ids() {
  local summary_json="$1"
  if [[ ! -f "$summary_json" ]]; then
    return
  fi
  python3 - "$summary_json" >> "$PRESERVED_RUN_IDS_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text())
for item in payload.get("runs", []):
    if isinstance(item, dict):
        run_id = item.get("run_id")
        if isinstance(run_id, str) and run_id:
            print(run_id)
PY
}

is_preserved_run_id() {
  local run_id="$1"
  grep -qx "$run_id" "$PRESERVED_RUN_IDS_FILE"
}

# Archive known stale/non-canonical artifact roots.
move_if_exists "$ROOT_DIR/jobs"
move_if_exists "$ROOT_DIR/orchestrator/workspace-high"
move_if_exists "$ROOT_DIR/orchestrator/results/jobs"

# Archive stale Harbor task bundles and repeat workspaces.
shopt -s nullglob
for stale_bundle in "$ROOT_DIR"/orchestrator/workspace/harbor-task-*; do
  move_if_exists "$stale_bundle"
done
for repeat_workspace in "$ROOT_DIR"/orchestrator/workspace-repeat-*; do
  move_if_exists "$repeat_workspace"
done
shopt -u nullglob

# Archive legacy flat result payloads (results/<run_id>.json + results/<run_id>/).
shopt -s nullglob
for flat_json in "$ROOT_DIR"/orchestrator/results/[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f].json; do
  run_id="$(basename "$flat_json" .json)"
  move_if_exists "$flat_json"
  move_if_exists "$ROOT_DIR/orchestrator/results/$run_id"
done
shopt -u nullglob

# Prune repeat suites: keep latest N per model slug.
SUITES_DIR="$ROOT_DIR/orchestrator/results/suites"
if [[ -d "$SUITES_DIR" ]]; then
  KEPT_SUITES_FILE="$(mktemp)"
  : > "$KEPT_SUITES_FILE"
  get_suite_count() {
    local key="$1"
    local value
    value="$(awk -F ',' -v k="$key" '$1 == k { print $2 }' "$KEPT_SUITES_FILE" | tail -n 1)"
    if [[ -z "$value" ]]; then
      echo "0"
      return
    fi
    echo "$value"
  }
  set_suite_count() {
    local key="$1"
    local next="$2"
    awk -F ',' -v k="$key" '$1 != k { print $0 }' "$KEPT_SUITES_FILE" > "$KEPT_SUITES_FILE.tmp"
    printf '%s,%s\n' "$key" "$next" >> "$KEPT_SUITES_FILE.tmp"
    mv "$KEPT_SUITES_FILE.tmp" "$KEPT_SUITES_FILE"
  }

  while IFS= read -r suite_dir; do
    suite_name="$(basename "$suite_dir")"
    model_slug="${suite_name%__x*}"
    model_slug="${model_slug##*__}"
    count="$(get_suite_count "$model_slug")"
    if (( count < KEEP_PER_MODEL )); then
      set_suite_count "$model_slug" "$((count + 1))"
      record_suite_run_ids "$suite_dir/summary.json"
      continue
    fi
    rel="${suite_dir#$ROOT_DIR/}"
    mkdir -p "$ARCHIVE_DIR/$(dirname "$rel")"
    mv "$suite_dir" "$ARCHIVE_DIR/$rel"
    echo "pruned: $rel"
  done < <(find "$SUITES_DIR" -mindepth 1 -maxdepth 1 -type d -print | sort -r)
fi

# Prune canonical runs: keep latest N per model slug, but preserve run IDs
# referenced by kept suite summaries.
RUNS_DIR="$ROOT_DIR/orchestrator/results/runs"
if [[ -d "$RUNS_DIR" ]]; then
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
  while IFS= read -r run_dir; do
    instance="$(basename "$run_dir")"
    run_id="$(echo "$instance" | awk -F'__' '{print $2}')"
    if [[ -n "$run_id" ]] && is_preserved_run_id "$run_id"; then
      continue
    fi
    model_slug="${instance##*__}"
    count="$(get_count "$model_slug")"
    if (( count < KEEP_PER_MODEL )); then
      set_count "$model_slug" "$((count + 1))"
      continue
    fi
    rel="${run_dir#$ROOT_DIR/}"
    mkdir -p "$ARCHIVE_DIR/$(dirname "$rel")"
    mv "$run_dir" "$ARCHIVE_DIR/$rel"
    echo "pruned: $rel"
  done < <(find "$RUNS_DIR" -mindepth 1 -maxdepth 1 -type d -print | sort -r)
fi

echo "archive_dir=$ARCHIVE_DIR"
