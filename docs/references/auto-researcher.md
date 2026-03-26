# Auto-Researcher

`auto_researcher` is an objective-led optimization workflow that sits above the base experiment surface:

- It creates a canonical scenario draft for a measurable goal.
- It seeds a pinned benchmark baseline.
- It runs bounded research loops and updates the best known benchmark when improvements are confirmed.

The loop is lightweight by design: it wraps deterministic experiment execution and keeps the same artifact contracts used by normal Raidar runs.

## 1. Core Components

- `auto_researcher/objectives/<objective-id>/`: lifecycle state (`objective.yaml`), brief, report, plans, and loop state.
- `auto_researcher/roles/`: planner/critic/runner prompt definitions.
- `scenarios/`: canonical scenario once approved (copied from objective draft space).
- `experiments/benchmarks/`: pinned benchmark experiments for each objective.
- `experiments/research_loops/`: loop experiments generated while executing objective improvements.

## 2. Public Workflow

Use the public make surface:

```bash
make auto-research-init GOAL="..." TARGET_HARNESS=codex-cli TARGET_MODEL=codex/gpt-5.4-mini
make auto-research-approve-scenario OBJECTIVE_ID=...
make auto-research-run OBJECTIVE_ID=...
make auto-research-status OBJECTIVE_ID=...
make auto-research-report OBJECTIVE_ID=...
```

Optional controls from the Make targets:
- `MAX_REVISIONS` (default `3`)
- `BENCHMARK_REPEATS` and `BENCHMARK_REPEAT_PARALLEL` (default `5`, `1`)
- `RESEARCH_REPEATS` and `RESEARCH_REPEAT_PARALLEL` (default `3`, `1`)
- `MAX_PARALLEL_LOOPS` (default `3`)

## 3. What "benchmark" Means

In auto-researcher, a benchmark is the current pinned reference for comparisons.

- Stored as an experiment with `experiment_kind=benchmark` under `experiments/benchmarks/`.
- Tracked in objective state as `best_benchmark_ref`.
- Used to compare future loop outputs for relative and absolute movement.

## 4. What "research loop" Means

- Stored as `experiment_kind=research-loop` experiments under `experiments/research_loops/`.
- Executed after scenario approval once an objective is active.
- Can generate candidate improvements, and a successful candidate may update benchmark state in the objective.

## 5. Inspecting Evidence

- `make auto-research-status OBJECTIVE_ID=...`: objective status, scenario refs, benchmark refs, and loop states.
- `auto-researcher status --objective-id ... --json`: machine-readable status payload including loop states and stop reason.
- `make auto-research-report OBJECTIVE_ID=...`: markdown summary of progress and active loops.
- `experiments/benchmarks/*` and `experiments/research_loops/*`: canonical `experiment-summary.json`, `experiment.json`, `report.md`, and run artifacts.

Keep in mind: use benchmark experiments to define current performance baselines, and use research-loop experiments for bounded iterations toward improvement.
