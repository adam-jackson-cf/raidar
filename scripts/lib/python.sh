#!/usr/bin/env bash
set -euo pipefail

resolve_python_cmd() {
  local candidate
  for candidate in python3 python; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
      >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done

  echo "Error: Python 3.11+ is required but no suitable interpreter was found." >&2
  return 1
}

run_with_python() {
  local python_cmd
  python_cmd="$(resolve_python_cmd)"
  "$python_cmd" "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  if [[ $# -eq 0 ]]; then
    resolve_python_cmd
    exit 0
  fi
  run_with_python "$@"
fi
