# Analyze Latest Agent Eval Results

Use this prompt to analyze the latest Harbor-orchestrated eval suites for each agent combination (`harness + model`) and produce a deterministic comparison report.

## Prompt

You are analyzing agent-eval outcomes for design-implementation tasks.

### Objective

Produce a deterministic comparison of the latest suite for each agent combination, then generate exhaustive, impact-ranked recommendations to improve score outcomes.

Focus on:

1. Cross-agent comparison (which combination performs best and why).
2. Per-agent localized improvements across:
   - scaffold (`AGENTS.md` rules, quality gates, verification commands)
   - task prompt iteration strategy
   - bespoke tools (when they improve deterministic outcomes)
3. Actionable next experiments with one-variable-at-a-time design.

### Repository Inputs

Use these paths:

- Suite summaries: `/Users/adamjackson/Projects/typescript-ui-eval/orchestrator/results/suites/*/summary.json`
- Suite readmes: `/Users/adamjackson/Projects/typescript-ui-eval/orchestrator/results/suites/*/README.md`
- Run summary artifacts: `/Users/adamjackson/Projects/typescript-ui-eval/orchestrator/results/runs/*/summary/result.json`
- Verifier artifacts: `/Users/adamjackson/Projects/typescript-ui-eval/orchestrator/results/runs/*/verifier/scorecard.json`
- Qualification artifacts: `/Users/adamjackson/Projects/typescript-ui-eval/orchestrator/results/runs/*/verifier/qualification.json`
- Agent traces: `/Users/adamjackson/Projects/typescript-ui-eval/orchestrator/results/runs/*/agent/trajectory.json`
- Agent logs: `/Users/adamjackson/Projects/typescript-ui-eval/orchestrator/results/runs/*/agent/codex.txt`

### Suite Selection Rule

For each unique `(task_name, harness, model)` combination:

1. Select the latest suite by `created_at_utc`.
2. Analyze only that suite for ranking.
3. Use run-level artifacts linked in the suite `runs[].summary_result_json` and `runs[].canonical_run_dir`.

### Gate-First Interpretation

Treat completion and deterministic quality criteria as first-class requirements.

1. Determine suite validity:
   - `retry.target_met == true`
   - `retry.unresolved_void_count == 0`
   - `aggregate.run_count_scored >= config.repeats`
2. If any validity gate fails:
   - mark suite `INVALID_FOR_RANKING`
   - set final weighted score to `0.0`
   - still report diagnostics and recommendations

### Strict Weighted Score (Deterministic)

For each valid suite, compute a strict weighted score in `[0, 100]` using normalized components:

`final_score = 100 * (0.40*success + 0.25*quality + 0.15*reliability + 0.10*speed + 0.10*cost)`

Component definitions:

- `success = aggregate.qualification_rate`
- `quality = aggregate.composite_score.mean`
- `reliability = 1 - (aggregate.void_count / aggregate.run_count_total)`
- `speed = inverse_normalized(aggregate.duration_sec.mean)`
- `cost = inverse_normalized(aggregate.uncached_input_tokens.mean)`

Normalization rules:

1. Use min-max normalization across compared valid suites within the same task.
2. For `speed` and `cost`, lower is better (`inverse_normalized`).
3. If all values are identical for a component, set that component to `1.0` for all suites.
4. Round displayed component values to 4 decimals and `final_score` to 2 decimals.

### Required Diagnostics

For each suite and for cross-agent comparison, compute and report:

1. Qualification profile:
   - `qualification_rate`
   - `qualified_count / run_count_scored`
2. Reliability profile:
   - `void_count`, `repeat_required_count`, retry usage
   - void reason frequencies from `runs[].void_reasons`
3. Efficiency profile:
   - `duration_sec.mean/median/stddev`
   - `uncached_input_tokens.mean/median/stddev`
4. Process quality profile from run-level `scores.metadata.process`:
   - mean `failed_command_count`
   - mean `verification_rounds`
   - mean `repeated_verification_failures`
   - mean `missing_required_verification_commands`
   - distribution of `failed_command_categories`
5. Deterministic-check profile from qualification and scorecard:
   - failing qualification checks frequency
   - failing compliance rules frequency
   - requirement gap frequency (`requirement_gap_ids`, `requirement_pattern_gaps`)

### Evidence Rules

For every material claim:

1. Provide abbreviated evidence (one short line).
2. Provide a direct artifact path.
3. Prefer suite-level evidence first, then run-level evidence for detail.

### Recommendation Rules

Produce exhaustive recommendations, ranked by expected impact (highest first).

For each recommendation, include:

1. `scope`: `global` or specific `(harness, model)` agent.
2. `lever`: `scaffold`, `prompt`, or `tooling`.
3. `change`: exact proposed adjustment.
4. `expected_metric_effect`: explicit metrics expected to improve.
5. `risk_to_determinism`: concrete risk and mitigation.
6. `experiment_design`: one-variable-at-a-time A/B test with success criteria.
7. `priority`: `P0`, `P1`, `P2`, `P3`.

Do not recommend relaxing deterministic checks or scoring criteria.

### Output Format

Return a report with these sections:

1. `## Ranked Agents (Latest Suite Per Combination)`
2. `## Scoring Breakdown`
3. `## Reliability and Failure Anatomy`
4. `## Per-Agent Insights`
5. `## Ranked Recommendations (Exhaustive)`
6. `## Suggested Experiment Backlog`
7. `## Contradictions and Knock-On Effects`

In `Ranked Agents`, include a table with:

- task
- harness
- model
- suite_id
- validity_status
- success
- quality
- reliability
- speed
- cost
- final_score

In `Suggested Experiment Backlog`, include numbered experiments with:

1. hypothesis
2. change
3. fixed controls
4. measurement window
5. pass/fail criteria

### Hard Constraints

1. Never treat deterministic-check failures as harness defects.
2. Always treat orchestrator implementation failures separately from task scoring failures.
3. Never relax thresholds, qualification checks, or scoring criteria during analysis.
4. If evidence is missing, state exactly which artifact path is missing and continue with available deterministic evidence.
