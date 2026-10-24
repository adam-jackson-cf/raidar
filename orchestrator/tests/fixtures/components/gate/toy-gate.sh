#!/usr/bin/env bash
# Toy commit gate for the guardrail-gate@1 real-trial test (stands in for a
# guardrails pack's gate script): it fails on odd runs and passes on even ones,
# and reports how many commits the workspace repository holds so far.
count_file=/tmp/raidar-toy-gate.count
n=$(( $(cat "$count_file" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$count_file"
echo "toy-gate run $n; commits so far: $(git rev-list --count HEAD 2>/dev/null || echo 0)"
if [ $((n % 2)) -eq 1 ]; then
  echo "gate-check planted-failure fail"
  exit 1
fi
echo "gate-check planted-failure pass"
