# Raidar per-step context reset body (requirements F3).
#
# Harbor runs steps/<name>/workdir/setup.sh as the step's agent user after the
# step's workdir upload and before the agent starts. raidar.subjects.treatments.runtime.setup_script
# inlines this body into every generated setup.sh. It:
#   1. kills every leftover process owned by the agent user (except this
#      script's own ancestry and helpers), so nothing from step k-1 survives;
#      the agent user must not be root (refused and reported, never killed);
#   2. resets harness-persisted state (Codex CODEX_HOME / ~/.codex / ~/.agents,
#      Claude CLAUDE_CONFIG_DIR / ~/.claude / ~/.claude.json, component and
#      workflow scratch dirs) to the Condition baseline in
#      $RAIDAR_HARNESS_BASELINE_DIR (removed when there is no baseline entry).
# Env overrides exist so the wipe path can be exercised on a host without
# touching real harness state.

_now_ms() { python3 -c 'import time;print(int(time.time()*1000))' 2>/dev/null || date +%s000; }
RAIDAR_T0=$(_now_ms)

HOME_DIR="${HOME:-/tmp/agent_home}"
BASELINE_DIR="${RAIDAR_HARNESS_BASELINE_DIR:-/opt/raidar/harness-baseline}"
LOGS_DIR="${RAIDAR_LOGS_DIR:-/logs/agent}"
REPORT_PATH="$LOGS_DIR/raidar-reset.json"
SKIP_KILL="${RAIDAR_RESET_SKIP_KILL:-0}"
KILL_UID="${RAIDAR_RESET_KILL_UID:-$(id -u)}"
# Killing every root-owned process would end the container (and the trial), so
# a root agent user gets no process reset: the report carries an error and the
# reset probe marks the step infra-invalid. Tasks must set a non-root [agent].user.
RESET_ERROR=""
if [ "$KILL_UID" = "0" ] && [ "$SKIP_KILL" != "1" ]; then
  RESET_ERROR="agent user is root (uid 0): leftover processes cannot be killed without ending the container; run the agent as a non-root [agent].user"
fi

# target_path|baseline_entry|kind(dir|file)
STATE_TARGETS=(
  "${RAIDAR_CODEX_HOME:-/tmp/codex-home}|codex-home|dir"
  "$HOME_DIR/.codex|codex-home|dir"
  "$HOME_DIR/.agents|agents-home|dir"
  "${RAIDAR_CLAUDE_CONFIG_DIR:-$LOGS_DIR/sessions}|claude-config|dir"
  "$HOME_DIR/.claude|claude-config|dir"
  "$HOME_DIR/.claude.json|claude.json|file"
  "${RAIDAR_WORKFLOW_DIR:-/tmp/raidar-review}|workflow|dir"
  "${RAIDAR_COMPONENT_ROOT:-/tmp/raidar-components}|components|dir"
)

json_escape() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/ }; s=${s//$'\t'/ }; printf '%s' "$s"; }

# ---- 1. kill leftover agent-user processes --------------------------------
# Protected set = this script's ancestors (the docker exec chain), its own
# descendants (transient ps/subshell helpers) and PID 1. Everything else owned
# by KILL_UID is a leftover from an earlier step.
declare -A ppid_of=() protected=() desc=()
_protect_from_snapshot() {  # $1 = "pid ppid uid args" lines
  ppid_of=(); protected=([1]=1); desc=()
  local p pp rest pid changed
  while read -r p pp rest; do [ -n "$p" ] && ppid_of[$p]=$pp; done <<<"$1"
  pid=$$
  while [ -n "$pid" ] && [ "$pid" != "0" ] && [ -z "${protected[$pid]:-}" ]; do
    protected[$pid]=1; pid=${ppid_of[$pid]:-}
  done
  protected[$$]=1
  changed=1
  while [ $changed = 1 ]; do
    changed=0
    for p in "${!ppid_of[@]}"; do
      if [ -z "${protected[$p]:-}" ] && { [ "${ppid_of[$p]}" = "$$" ] || [ -n "${desc[${ppid_of[$p]}]:-}" ]; }; then
        protected[$p]=1; desc[$p]=1; changed=1
      fi
    done
  done
}
_scan() {  # $1 = kill|residual ; prints JSON objects
  local snap p pp u args out=""
  snap=$(ps -e -o pid=,ppid=,uid=,args= 2>/dev/null)
  _protect_from_snapshot "$snap"
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
killed_json=""
residual_json=""
if [ "$SKIP_KILL" != "1" ] && [ -z "$RESET_ERROR" ]; then
  scratch="${TMPDIR:-/tmp}/.raidar-scan.$$"
  _scan kill >"$scratch"; killed_json=$(cat "$scratch")
  sleep 0.2
  _scan residual >"$scratch"; residual_json=$(cat "$scratch")
  rm -f "$scratch"
fi

# ---- 2. reset harness state to the Condition baseline ----------------------
state_json=""
declare -A seen=()
for entry in "${STATE_TARGETS[@]}"; do
  IFS='|' read -r target base kind <<<"$entry"
  [ -n "${seen[$target]:-}" ] && continue
  seen[$target]=1
  existed=false
  { [ -e "$target" ] || [ -L "$target" ]; } && existed=true
  rm -rf -- "$target" 2>/dev/null
  restored=false
  if [ -e "$BASELINE_DIR/$base" ]; then
    if [ "$kind" = "dir" ]; then
      mkdir -p -- "$target" && cp -a -- "$BASELINE_DIR/$base/." "$target/" && restored=true
    else
      mkdir -p -- "$(dirname -- "$target")" && cp -a -- "$BASELINE_DIR/$base" "$target" && restored=true
    fi
  fi
  reset_failed=false
  if [ "$restored" = false ] && { [ -e "$target" ] || [ -L "$target" ]; }; then
    reset_failed=true
  fi
  state_json+="${state_json:+,}{\"path\":\"$(json_escape "$target")\",\"existed\":$existed,\"restored_from_baseline\":$restored,\"reset_failed\":$reset_failed}"
done

RAIDAR_T1=$(_now_ms)
mkdir -p -- "$(dirname -- "$REPORT_PATH")" 2>/dev/null
cat >"$REPORT_PATH" <<JSON
{"kind":"raidar-step-reset","uid":$(id -u),"kill_uid":$KILL_UID,"skip_kill":$([ "$SKIP_KILL" = 1 ] && echo true || echo false),"started_ms":$RAIDAR_T0,"finished_ms":$RAIDAR_T1,"duration_ms":$((RAIDAR_T1 - RAIDAR_T0)),"error":$([ -n "$RESET_ERROR" ] && printf '"%s"' "$(json_escape "$RESET_ERROR")" || echo null),"killed":[${killed_json}],"residual":[${residual_json}],"state":[${state_json}],"baseline_dir":"$(json_escape "$BASELINE_DIR")"}
JSON
