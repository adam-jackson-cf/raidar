---
name: "experiment-design"
description: "Use when authoring or revising a Raidar experiment's conditions, arms, problem pins or analysis plan."
---

# Task

## Design

- Read the existing experiment before editing; use experiment-init only for a brand-new path.
- Choose explore unless the user explicitly requests confirm; smoke is operational health, neither objective.
- Use schema_version: 2 and one experiment.yaml at lab/experiments/<kind>/<runtime>__<subject>__<intent>/.
- Declare one runtime, pinned problems, agents, conditions, arms, assessment and analysis.
  Put shared_components ahead of each condition's own components, identically.
- Use ab-test for conditions (factors include condition), benchmark for agents under one condition
  (agent.* factors only), and smoke for operational checks. research-loop is reserved.
- In a benchmark make the reference agent the baseline and declare ranking intent.
- Select problems with `make problem-list` and `make problem-info PROBLEM=<ref>`.
  Import pinned bindings with `make problem-import PROBLEM=<ref>`;
  run `make problem-validate PROBLEM=<ref>` when the binding lacks a validation report.
- Default real-LLM design: codex, gpt-6-luna, low reasoning effort; agent versions equal pack pins.
- Set analysis.baseline_arm, attempts, seed and verdict thresholds.
  Declare analysis.primary_outcome when overriding solve_rate/reached;
  show the raw counterpart beside an errata-applied primary.

## Explore

- Start with the smallest check that can fail: no-LLM fixture, orchestrator-smoke or 1–2 trials of the tested arm.
- Use at most 4 attempts per arm, one problem and the default agent.
- Lock and pre-register; a power target is not required.
- Stop on process defects; fix and rerun small before adding attempts.
- Report exploratory directions, process/component signals, faults and cost, never evidence of an effect.
  Below min_pairs the verdict is insufficient.

## Confirm

- Require a clean explore run of the exact design and no pending verifier, scorer, component, gate or errata changes.
- Before the first job pre-register the primary outcome in rationale.md, thresholds in the lock and attempts per arm.
  Size attempts from earlier explore jobs' J2 sample-size projection for that primary.
- Run every arm under one lock without changes mid-series; allow at most 1 infrastructure exclusion per arm.
  A Raidar or treatment change starts a new experiment rather than patching the series.
- Report confidence intervals, cost beside effects, gate effort and quality; only confirm results are citable.

## Validation and output

- Run `make experiment-validate EXPERIMENT=<path>`, then experiment-lock and experiment-plan with the same EXPERIMENT.
- Correct compatibility errors at their named fields; never hand-edit lock files.
- Return the objective, design path, resolved arms, validation results and whether execution was requested.
