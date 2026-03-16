# Analyze Results

Use this guide to analyze the latest experiment for each
`(scenario_name, scenario_revision, harness, model, evaluation_profile)`
combination and produce a deterministic comparison report.

Metric definitions, prerequisites, and interpretation notes live in
[metrics.md](/Users/adamjackson/Projects/raidar/docs/references/metrics.md).
This guide stays focused on artifact review, ranking, and recommendation
workflow.

`AgentSpec` means `harness + model`.

## Objective

Produce a deterministic comparison of the latest experiment for each
combination, then generate exhaustive, impact-ranked recommendations to
improve score outcomes.

Focus on:

1. Cross-agent comparison: which combination performs best and why.
2. Per-agent localized improvements across:
   - scaffold (`AGENTS.md` rules, quality gates, verification commands)
   - task prompt iteration strategy
   - bespoke tools when they improve deterministic outcomes
3. Actionable next experiments with one-variable-at-a-time design.

## Canonical Inputs

Use only these artifact paths:

- Experiment records: `experiments/*/experiment.json`
- Experiment summaries: `experiments/*/experiment-summary.json`
- Experiment reports: `experiments/*/report.md`
- Run records: `experiments/*/runs/*/run.json`
- Run reports: `experiments/*/runs/*/report.md`
- Verifier scorecards: `experiments/*/runs/*/verifier/scorecard.json`
- Execution-validity artifacts:
  `experiments/*/runs/*/verifier/execution-validity.json`
- Performance-gates artifacts:
  `experiments/*/runs/*/verifier/performance-gates.json`
- Harness traces: `experiments/*/runs/*/harness/*.trajectory.json`
- Harness logs: `experiments/*/runs/*/harness/*.txt`

Do not read from legacy `evals/`, `results/`, or other pre-experiment roots.

## Experiment Selection Rule

For each unique
`(scenario_name, scenario_revision, harness, model, evaluation_profile)`:

1. Read identity from `experiment-summary.json.config`.
2. Select the latest experiment by `created_at_utc`.
3. Analyze only that latest experiment for ranking.
4. Use run-level artifacts linked in `experiment-summary.json.runs[]` and the
   canonical run directories under `experiments/*/runs/*`.

Treat `evaluation_profile` plus `metrics` as the capability identity for
comparisons. Do not collapse different metric sets into one benchmark row.

## Gate-First Interpretation

Treat completion and deterministic validity criteria as first-class
requirements.

For each latest experiment, compute and report both statuses:

1. `ranking_status`
   - `RANKABLE` when all are true:
     - `rerun.target_met == true`
     - `rerun.unresolved_unscored_count == 0`
     - `aggregate.run_count_scored >= config.repeats`
     - `aggregate.validity_rate == 1.0`
   - `INVALID_FOR_RANKING` otherwise
2. `quality_status`
   - based on scored quality outcomes from:
     - `run.json.scores.functional`
     - `run.json.scores.acceptance`
     - `run.json.scores.visual` when present
     - `run.json.scores.verification_stability`
     - `experiment-summary.json.aggregate.metric_outcomes`

If an experiment is `INVALID_FOR_RANKING`:

- keep it in the report
- rank it below all `RANKABLE` experiments
- use `aggregate.composite_score.mean` only as diagnostic context, not as a
  justification to override invalidity

## Benchmark Score Interpretation

Use the implementation's current summary fields instead of inventing a legacy
weighted score.

For each latest experiment, treat these fields as the benchmark inputs:

- Ranking benchmark: `experiment-summary.json.aggregate.composite_score.mean`
- Quality benchmark: `experiment-summary.json.aggregate.quality_score.mean`
- Diagnostic benchmark: `experiment-summary.json.aggregate.diagnostic_score.mean`
- Validity benchmark: `experiment-summary.json.aggregate.validity_rate`
- Performance benchmark:
  `experiment-summary.json.aggregate.performance_pass_rate`
- Efficiency benchmarks:
  - `experiment-summary.json.aggregate.duration_sec.mean`
  - `experiment-summary.json.aggregate.uncached_input_tokens.mean`

Primary sort order:

1. `ranking_status` (`RANKABLE` before `INVALID_FOR_RANKING`)
2. `aggregate.composite_score.mean` descending
3. `aggregate.quality_score.mean` descending
4. `aggregate.validity_rate` descending
5. `created_at_utc` descending

When a benchmark input is missing:

1. State the missing artifact path and field.
2. Keep the experiment in the comparison.
3. Do not fabricate substitute values.
4. Downgrade confidence for the affected comparison claim.

## Required Fields

Always report these exact fields when present:

- `experiment-summary.json.config.scenario_name`
- `experiment-summary.json.config.scenario_revision`
- `experiment-summary.json.config.harness`
- `experiment-summary.json.config.model`
- `experiment-summary.json.config.evaluation_profile`
- `experiment-summary.json.config.metrics`
- `experiment-summary.json.config.repeats`
- `experiment-summary.json.aggregate.run_count_scored`
- `experiment-summary.json.aggregate.valid_count`
- `experiment-summary.json.aggregate.validity_rate`
- `experiment-summary.json.aggregate.performance_pass_count`
- `experiment-summary.json.aggregate.performance_pass_rate`
- `experiment-summary.json.aggregate.composite_score`
- `experiment-summary.json.aggregate.quality_score`
- `experiment-summary.json.aggregate.diagnostic_score`
- `experiment-summary.json.aggregate.duration_sec`
- `experiment-summary.json.aggregate.uncached_input_tokens`
- `experiment-summary.json.aggregate.metric_outcomes`
- `experiment-summary.json.rerun.target_met`
- `experiment-summary.json.rerun.unresolved_unscored_count`
- `run.json.config.evaluation_profile`
- `run.json.scores.functional`
- `run.json.scores.acceptance`
- `run.json.scores.visual`
- `run.json.scores.verification_stability`
- `run.json.scores.execution_validity`
- `run.json.scores.performance_gates`
- `run.json.scores.resource_efficiency`
- `run.json.scores.requirements_coverage`
- `run.json.scores.test_coverage`
- `run.json.scores.metric_results[]`
- `run.json.scores.metadata.process`

## Supported Metrics

Use only the current metric ids:

- `functional`
- `acceptance`
- `verification-stability`
- `execution-validity`
- `resource-efficiency`
- `test-coverage`
- `requirements-coverage`
- `llm-judge`
- `visual-regression`
- `artifact-checks`

Treat `run.json.scores.metric_results[]` as module output for configured
non-core metrics and audit-style extensions. Core metrics live on their named
score fields.

## Required Diagnostics

For each experiment and for cross-agent comparison, compute and report:

1. Identity profile:
   - `scenario_name`
   - `scenario_revision`
   - `harness`
   - `model`
   - `evaluation_profile`
   - configured `metrics`
2. Ranking profile:
   - `ranking_status`
   - `quality_status`
   - `run_count_scored / repeats`
   - `valid_count / run_count_scored`
   - `performance_pass_count / run_count_scored`
3. Benchmark profile:
   - `composite_score.mean`
   - `quality_score.mean`
   - `diagnostic_score.mean`
   - `duration_sec.mean/median/stddev`
   - `uncached_input_tokens.mean/median/stddev`
4. Process quality profile from `run.json.scores.metadata.process`:
   - mean `command_count`
   - mean `failed_command_count`
   - mean `process_failed_command_count`
   - mean `verification_rounds`
   - mean `repeated_verification_failures`
   - mean `missing_required_verification_commands`
   - mean required-verification execution rate:
     `executed_required_verification_commands / required_verification_commands`
   - distribution of `failed_command_categories`
5. Deterministic-check profile from run score fields and verifier artifacts:
   - failing `execution_validity.checks` frequency
   - failing `performance_gates.checks` frequency
   - failing acceptance checks frequency
   - requirement gap frequency from
     `requirements_coverage.requirement_gap_ids` and
     `requirements_coverage.requirement_pattern_gaps`
   - metric outcome pass/fail counts from
     `experiment-summary.json.aggregate.metric_outcomes`
6. Harness log pattern profile:
   - command execution and failure motifs
   - verification loop behavior (`run`, `fix`, `re-run` cycles)
   - tool usage breadth and repeated failure patterns
   - incomplete or aborted turn signatures

## Evidence Rules

For every material claim:

1. Provide abbreviated evidence in one short line.
2. Provide a direct artifact path.
3. Prefer experiment-level evidence first, then run-level evidence for detail.
4. For log-derived claims, apply the same pattern categories across harnesses;
   do not use harness-specific grading standards.

## Recommendation Rules

Produce exhaustive recommendations, ranked by expected impact highest first.

For each recommendation, include:

1. `scope`: `global` or a specific
   `(scenario_name, scenario_revision, harness, model, evaluation_profile)`
2. `lever`: `scaffold`, `prompt`, or `tooling`
3. `change`: exact proposed adjustment
4. `expected_metric_effect`: explicit metrics or statuses expected to improve
5. `risk_to_determinism`: concrete risk and mitigation
6. `experiment_design`: one-variable-at-a-time A/B test with success criteria
7. `priority`: `P0`, `P1`, `P2`, or `P3`

Do not recommend relaxing deterministic checks, gate thresholds, or scoring
criteria.

## Output Format

Return a report with these sections:

1. `## Ranked Agents (Latest Experiment Per Combination)`
2. `## Scoring Breakdown`
3. `## Reliability and Failure Anatomy`
4. `## Per-Agent Insights`
5. `## Ranked Recommendations (Exhaustive)`
6. `## Suggested Experiment Backlog`
7. `## Contradictions and Knock-On Effects`

In `Ranked Agents`, include a benchmark table with one row per latest
experiment and these columns:

- `scenario`
- `revision`
- `harness`
- `model`
- `evaluation_profile`
- `experiment_id`
- `ranking_status`
- `quality_status`
- `scored_runs/repeats`
- `valid_count`
- `validity_rate`
- `performance_pass_rate`
- `quality_score_mean`
- `diagnostic_score_mean`
- `composite_score_mean`
- `duration_mean_sec`
- `uncached_input_tokens_mean`
- `top_failure_modes`

In `Scoring Breakdown`:

- explain why the ordering follows `ranking_status` first and
  `composite_score.mean` second
- call out any metric families configured in `metrics` that materially changed
  comparability between rows
- separate benchmark interpretation from recommendation content

In `Suggested Experiment Backlog`, include numbered experiments with:

1. hypothesis
2. change
3. fixed controls
4. measurement window
5. pass/fail criteria

## Hard Constraints

1. Never treat deterministic-check failures as harness defects.
2. Always treat orchestrator implementation failures separately from scenario
   scoring failures.
3. Never relax thresholds, execution-validity checks, performance gates, or
   scoring criteria during analysis.
4. If evidence is missing, state exactly which artifact path and field are
   missing and continue with available deterministic evidence.
5. Treat `artifact-checks` as audit-only unless the configured experiment
   contract explicitly makes it gating.
6. Do not use the visual explainer skill or require visual companion output for
   this workflow.

## Output Artifact

Write any derived human review as:

- `experiments/eval-analysis-<scenario>-<YYYYMMDD-HHMMSS>.md`

Create the directory if needed.
