---
name: "raidar-performance"
description: "Use when checking Raidar execution overhead or proposing changes to accepted performance budgets."
---

# Task

## Measure

- Check `make target-status TARGET=<name>`, including the target data check.
- For release/phase or execution-path validation run `make perf-gate TARGET=<name>`.
  This strictly checks no-LLM fixtures at concurrency 1 and 3; it is separate from make quality.
- For a real job use `make perf-check JOB=<job-dir>`.
  This is report-only unless strict validation was requested with PERF_STRICT=1.
- Report the selected budget set, sample sufficiency, concurrency and contention beside breaches.
- Check jobs together only when they share a target.
  Use the budget set selected from recorded execution_target; missing/unrecognised targets use the reference set.
- Never rebaseline to pass a run. A budget structure or value change requires explicit user approval.
  The accepted baseline is maintained in the overhead-baseline document; request docs access before reading it.

## Output

- Return checked jobs/target, budget set, command result, breaches and whether the check was strict or report-only.
