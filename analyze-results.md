# Analyze Latest Agent Eval Results

Use this prompt to analyze the latest suite for each `(task_name, task_version, harness, model)` combination.

## Prompt

You are analyzing agent-eval outcomes for design-implementation tasks.

### Objective

Produce a deterministic comparison of the latest suite per combination, then generate impact-ranked recommendations that improve scored validity and optimization outcomes without relaxing gates.

### Repository Inputs

Use only these artifact paths:

- Suite records: `evals/*/suite.json`
- Suite summaries: `evals/*/suite-summary.json`
- Suite analysis docs: `evals/*/analysis.md`
- Run records: `evals/*/runs/*/run.json`
- Verifier scorecards: `evals/*/runs/*/verifier/scorecard.json`
- Run-validity artifacts: `evals/*/runs/*/verifier/run-validity.json`
- Performance-gates artifacts: `evals/*/runs/*/verifier/performance-gates.json`
- Pre-task screenshots: `evals/*/runs/*/homepage-pre.png`
- Post-task screenshots: `evals/*/runs/*/homepage-post.png`
- Agent traces: `evals/*/runs/*/agent/trajectory.json`
- Agent logs: `evals/*/runs/*/agent/*.txt`

Do not use non-canonical legacy roots outside `evals/`.

### Suite Selection Rule

For each unique `(task_name, task_version, harness, model)`:
1. Identify task version from run scorecards (`task_name`, `task_version`) in the suite.
2. Select the latest suite by `created_at_utc`.
3. Analyze only that latest suite for ranking.
4. Use per-run pointers from `suite.json` (`runs[].run_json_path`, `runs[].canonical_run_dir`) to collect all required run artifacts.

### Gate-First Interpretation

A suite is valid for ranking only when all are true:
- `retry.target_met == true`
- `retry.unresolved_void_count == 0`
- `aggregate.run_count_scored >= config.repeats`
- `aggregate.validity_rate == 1.0`
- every scored run has `run_valid == true`
- every scored run has `performance_gates_passed == true`

If any gate fails:
- mark suite status `INVALID_FOR_RANKING`
- set final ranking score to `0.0`
- still include diagnostics and recommendations

### Deterministic Ranking Score

For valid suites, compute:

`final_score = 100 * (0.45*optimization + 0.20*quality + 0.15*reliability + 0.10*speed + 0.10*cost)`

Where:
- `optimization = aggregate.composite_score.mean`
- `quality = aggregate.quality_score.mean`
- `reliability = 1 - (void_count / aggregate.run_count_total)`
- `speed = inverse_normalized(aggregate.duration_sec.mean)` (normalize across compared valid suites only)
- `cost = inverse_normalized(aggregate.uncached_input_tokens.mean)` (normalize across compared valid suites only)

### Required Sections

Return:
1. `## Ranked Agents (Latest Suite Per Task Version)`
2. `## Scoring Breakdown`
3. `## Reliability and Failure Anatomy`
4. `## Per-Agent Insights`
5. `## Ranked Recommendations (Exhaustive)`
6. `## Suggested Experiment Backlog`
7. `## Contradictions and Knock-On Effects`

### Hard Constraints

1. Never treat deterministic-check failures as harness defects.
2. Always separate orchestrator implementation failures from task scoring failures.
3. Never propose relaxing thresholds, deterministic checks, or scoring criteria.
4. If evidence is missing, list missing artifact paths and continue with available evidence.
5. Recommendations must explicitly reference the affected task version(s) (for example `homepage-implementation@v001`).
