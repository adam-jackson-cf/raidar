#!/usr/bin/env bash
set -euo pipefail

# Kills orphaned Harbor process trees and stale task containers that can block
# future Harbor runs.

if ! command -v ps >/dev/null 2>&1; then
  exit 0
fi

mapfile -t stale_pids < <(
  ps -ax -o pid=,ppid=,command= | awk '
    function is_build(cmd) {
      return cmd ~ /docker compose/ && cmd ~ /docker-compose-build\.yaml build/ ||
             cmd ~ /docker-compose compose/ && cmd ~ /docker-compose-build\.yaml build/ ||
             cmd ~ /docker-buildx bake/ && cmd ~ /harbor-task-[^\/]+\/environment/
    }
    function is_orphan_harbor_run(pid, ppid, cmd) {
      return (ppid <= 1) && (cmd ~ /harbor run --path/) && (cmd ~ /harbor-task-/)
    }
    function has_orphan_ancestor(pid,   current, guard) {
      current = ppid_by_pid[pid]
      guard = 0
      while (current > 1 && guard < 128) {
        if (current in orphan_run) {
          return 1
        }
        current = ppid_by_pid[current]
        guard++
      }
      return (current in orphan_run)
    }
    {
      pid = $1
      ppid = $2
      $1 = ""; $2 = ""
      sub(/^ +/, "", $0)
      cmd = $0
      ppid_by_pid[pid] = ppid
      cmd_by_pid[pid] = cmd
      if (is_orphan_harbor_run(pid, ppid, cmd)) {
        orphan_run[pid] = 1
      }
      if (is_build(cmd)) {
        build_pid[pid] = 1
      }
    }
    END {
      for (pid in orphan_run) {
        stale[pid] = 1
      }
      for (pid in build_pid) {
        if (ppid_by_pid[pid] <= 1 || has_orphan_ancestor(pid)) {
          stale[pid] = 1
        }
      }
      for (pid in stale) {
        print pid
      }
    }
  '
)

if (( ${#stale_pids[@]} > 0 )); then
  kill "${stale_pids[@]}" 2>/dev/null || true
  sleep 1
  for pid in "${stale_pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  echo "Cleaned stale Harbor processes: ${#stale_pids[@]}"
fi

if ! command -v docker >/dev/null 2>&1; then
  exit 0
fi

mapfile -t stale_containers < <(
  docker ps -a --format '{{.ID}}\t{{.Names}}\t{{.Status}}' | awk -F '\t' '
    $2 ~ /^harbor-task.*-main-1$/ || $2 ~ /^git-multibranch__.+-main-1$/ {
      if ($3 !~ /^Up /) {
        print $1
      }
    }
  '
)

if (( ${#stale_containers[@]} > 0 )); then
  docker rm -f "${stale_containers[@]}" >/dev/null 2>&1 || true
  echo "Cleaned stale Harbor containers: ${#stale_containers[@]}"
fi
