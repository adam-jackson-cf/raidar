# Agentic Eval System Research and Rationale

This document captures the rationale behind the current orchestrator design.

## Problem Statement

We need repeatable, comparable evaluation runs across harness/model pairs while preserving:
- deterministic task inputs,
- strict verification semantics,
- auditable artifacts for every run.

## Core Decisions

1. Build on Harbor for execution isolation and harness integration.
2. Keep scoring multidimensional (`functional`, `compliance`, `visual`, `efficiency`, plus hard gates).
3. Make task versions first-class (`tasks/<task>/v###`) so benchmark iterations are explicit.
4. Treat `task.yaml` as config, with prompts split into external artifacts.
5. Use a single execution root per suite to avoid split `results/jobs/workspace` ambiguity.

## Current Architecture Model

### Task Contract

Task versions include:
- `task.yaml` (config)
- `prompt/` artifacts (`prompt.entry`, optional includes)
- `rules/` single ruleset (agent-specific filename mapping)
- `scaffold/` root (task-local directory snapshot)

### Execution Contract

Outputs are grouped under:

`executions/<suite-id>/`

Containing:
- `runs/`
- `workspace/baseline/`
- `suite.json`
- `suite-summary.json`
- `analysis.md`

This enables one-directory archival, inspection, and sharing per suite instance.

### Matrix Contract

Matrix config now enumerates harness/model pairs directly.
No rules-variant cartesian expansion is used.

## Why This Is Better Than Previous Layout

Previous state had multiple top-level artifact roots and rules/scaffold variation split across unrelated paths. That made provenance harder to reason about and left stale empty folders.

Current state improves UX by:
- colocating correlated artifacts,
- reducing empty directory noise,
- minimizing hidden coupling between task config and implementation assets,
- making version iteration explicit and auditable.

## Known Tradeoffs

1. Task-local scaffold snapshots can drift if edited in-place without a new task version.
2. Runner remains large and should be further decomposed.
3. Execution retention still requires active cleanup policy.

## Next Engineering Priorities

1. Enforce `task clone-version` usage in team workflow for deterministic `v001 -> v002` promotion.
2. Add scaffold freeze tooling for fully immutable version snapshots.
3. Add stronger retention/compaction controls for long-running benchmark programs.
