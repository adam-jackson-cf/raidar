#!/usr/bin/env bash
set -euo pipefail

ORACLE_WRAPPER="${HOME}/.agents/skills/oracle/scripts/oracle-browser-run.sh"

if [[ ! -x "${ORACLE_WRAPPER}" ]]; then
  echo "Oracle wrapper not found or not executable: ${ORACLE_WRAPPER}" >&2
  exit 1
fi

exec "${ORACLE_WRAPPER}" "$@"
