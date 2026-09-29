#!/usr/bin/env bash
# Raidar per-step context reset (A' spike, plan task 0.3 / future C3).
#
# Harbor runs steps/<name>/workdir/setup.sh as the step's agent user before
# the agent starts (harbor.trial.multi_step._run_step_setup). This script:
#   1. kills every leftover process owned by the agent user (except its own
#      ancestry), so nothing started in step k-1 survives into step k;
#   2. resets harness-persisted state (Codex CODEX_HOME / ~/.codex, Claude
#      CLAUDE_CONFIG_DIR / ~/.claude / ~/.claude.json) to the Condition
#      baseline in $RAIDAR_HARNESS_BASELINE_DIR (empty when absent);
#   3. writes a JSON report and deletes itself from the workspace.
#
# Env overrides exist so the wipe path can be unit-checked on a host without
# touching real harness state (see tests/test_step_setup.py).
set -uo pipefail

_now_ms() { python3 -c 'import time;print(int(time.time()*1000))' 2>/dev/null || date +%s000; }
T0=$(_now_ms)

HOME_DIR="${HOME:-/tmp/agent_home}"
BASELINE_DIR="${RAIDAR_HARNESS_BASELINE_DIR:-/opt/raidar/harness-baseline}"
REPORT_PATH="${RAIDAR_RESET_REPORT:-/logs/agent/raidar-reset.json}"
SKIP_KILL="${RAIDAR_RESET_SKIP_KILL:-0}"
KILL_UID="${RAIDAR_RESET_KILL_UID:-$(id -u)}"

# target_path|baseline_subdir|kind(dir|file)
STATE_TARGETS=(
  "${RAIDAR_CODEX_HOME:-$HOME_DIR/.codex}|codex-home|dir"
  "$HOME_DIR/.codex|codex-home|dir"
  "${CLAUDE_CONFIG_DIR:-/logs/agent/sessions}|claude-config|dir"
  "$HOME_DIR/.claude|claude-config|dir"
  "$HOME_DIR/.claude.json|claude.json|file"
)

json_escape() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/ }; s=${s//$'\t'/ }; printf '%s' "$s"; }

# ---- 1. kill leftover agent-user processes --------------------------------
killed_json=""
residual_json=""
# Protected set = this script's ancestors (docker exec chain) + its own
# descendants (transient ps/subshell helpers) + PID 1. Everything else owned by
# KILL_UID is a leftover from an earlier step.
declare -A ppid_of=() protected=()
_protect_from_snapshot() {  # $1 = "pid ppid uid args" lines
  ppid_of=(); protected=([1]=1)
  local p pp u rest pid changed
  while read -r p pp u rest; do [ -n "$p" ] && ppid_of[$p]=$pp; done <<<"$1"
  pid=$$
  while [ -n "$pid" ] && [ "$pid" != "0" ] && [ -z "${protected[$pid]:-}" ]; do
    protected[$pid]=1; pid=${ppid_of[$pid]:-}
  done
  protected[$$]=1
  changed=1
  while [ $changed = 1 ]; do
    changed=0
    for p in "${!ppid_of[@]}"; do
      if [ -z "${protected[$p]:-}" ] && [ "${ppid_of[$p]}" = "$$" -o -n "${desc[${ppid_of[$p]}]:-}" ]; then
        protected[$p]=1; desc[$p]=1; changed=1
      fi
    done
  done
}
declare -A desc=()
_scan() {  # $1 = mode kill|residual ; emits JSON objects
  local snap p pp u args out=""
  snap=$(ps -e -o pid=,ppid=,uid=,args= 2>/dev/null)
  desc=(); _protect_from_snapshot "$snap"
  while read -r p pp u args; do
    [ -z "$p" ] && continue
    [ "$u" = "$KILL_UID" ] || continue
    [ -n "${protected[$p]:-}" ] && continue
    if [ "$1" = kill ]; then
      kill -KILL "$p" 2>/dev/null || continue
    fi
    out+="${out:+,}{\"pid\":$p,\"args\":\"$(json_escape "$args")\"}"
  done <<<"$snap"
  printf '%s' "$out"
}
if [ "$SKIP_KILL" != "1" ]; then
  _scan kill >"${TMPDIR:-/tmp}/.raidar-killed.$$"
  killed_json=$(cat "${TMPDIR:-/tmp}/.raidar-killed.$$"); rm -f "${TMPDIR:-/tmp}/.raidar-killed.$$"
  sleep 0.2
  _scan residual >"${TMPDIR:-/tmp}/.raidar-residual.$$"
  residual_json=$(cat "${TMPDIR:-/tmp}/.raidar-residual.$$"); rm -f "${TMPDIR:-/tmp}/.raidar-residual.$$"
fi

# ---- 2. reset harness state to baseline ----------------------------------
wiped_json=""
declare -A seen=()
for entry in "${STATE_TARGETS[@]}"; do
  IFS='|' read -r target base kind <<<"$entry"
  [ -n "${seen[$target]:-}" ] && continue
  seen[$target]=1
  existed=false
  [ -e "$target" ] || [ -L "$target" ] && existed=true
  rm -rf -- "$target" 2>/dev/null
  restored=false
  if [ -e "$BASELINE_DIR/$base" ]; then
    if [ "$kind" = "dir" ]; then
      mkdir -p -- "$target" && cp -a -- "$BASELINE_DIR/$base/." "$target/" && restored=true
    else
      mkdir -p -- "$(dirname -- "$target")" && cp -a -- "$BASELINE_DIR/$base" "$target" && restored=true
    fi
  fi
  still_foreign=false
  if [ "$restored" = false ] && { [ -e "$target" ] || [ -L "$target" ]; }; then
    still_foreign=true
  fi
  wiped_json+="${wiped_json:+,}{\"path\":\"$(json_escape "$target")\",\"existed\":$existed,\"restored_from_baseline\":$restored,\"reset_failed\":$still_foreign}"
done

T1=$(_now_ms)
mkdir -p -- "$(dirname -- "$REPORT_PATH")" 2>/dev/null
cat >"$REPORT_PATH" <<JSON
{"kind":"raidar-step-reset","uid":$(id -u),"kill_uid":$KILL_UID,"skip_kill":$([ "$SKIP_KILL" = 1 ] && echo true || echo false),"started_ms":$T0,"finished_ms":$T1,"duration_ms":$((T1 - T0)),"killed":[${killed_json}],"residual":[${residual_json}],"state":[${wiped_json}],"baseline_dir":"$(json_escape "$BASELINE_DIR")"}
JSON

# ---- 3. never leave this script in the agent's workspace -----------------
if [ "${RAIDAR_RESET_KEEP_SELF:-0}" != "1" ]; then
  rm -f -- "$0" 2>/dev/null
fi
exit 0
