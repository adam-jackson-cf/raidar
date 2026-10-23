#!/usr/bin/env bash
# Run the no-LLM perf fixture at concurrency 1 then 3 and collect a summary.
# Usage: run_arm.sh <arm-name> <out-dir>
# Env:   DOCKER_CONTEXT (default orbstack), RAIDAR_COPY_MODE (0/1), RAIDAR_DATA_CHECK,
#        ARM_CONCURRENCIES (default "1 3"), plus anything the fixture reads.
set -uo pipefail
arm="${1:?arm name}"
out="$(mkdir -p "${2:?out dir}" && cd "$2" && pwd)"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
context="${DOCKER_CONTEXT:-orbstack}"
export DOCKER_CONTEXT="$context" RAIDAR_EXECUTION_DOCKER=1
unset AGENT

rm -f "$out/.watch-stop"
python3 "$here/watch_mounts.py" "$context" "$out/mounts.jsonl" "$out/.watch-stop" "$out" &
watcher=$!
status=0
for c in ${ARM_CONCURRENCIES:-1 3}; do
  dir="$out/concurrency-$c"
  rm -rf "$dir"
  echo "== arm $arm: concurrency $c (context=$context copy=${RAIDAR_COPY_MODE:-0}) -> $dir"
  (cd "$repo/orchestrator" && RAIDAR_PERF_FIXTURE_DIR="$dir" RAIDAR_PERF_FIXTURE_CONCURRENCY="$c" \
    uv run --project . --extra dev python -m pytest tests/test_execution_perf_fixture.py -v -s -rs \
    >"$out/concurrency-$c.log" 2>&1)
  rc=$?
  echo "   pytest exit $rc"
  echo "$rc" >"$out/concurrency-$c.exit"
  [ "$rc" -eq 0 ] || status=$rc
done
touch "$out/.watch-stop"
wait "$watcher"
python3 "$here/collect.py" "$out" --arm "$arm"
exit "$status"
