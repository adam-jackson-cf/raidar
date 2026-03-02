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
- Agent traces: `evals/*/runs/*/agent/*.trajectory.json`
- Agent logs: `evals/*/runs/*/agent/*.txt`

Do not use non-canonical legacy roots outside `evals/`.

### Suite Selection Rule

For each unique `(task_name, task_version, harness, model)`:
1. Identify task version from run scorecards (`task_name`, `task_version`) in the suite.
2. Select the latest suite by `created_at_utc`.
3. Analyze only that latest suite for ranking.
4. Use per-run pointers from `suite.json` (`runs[].run_json_path`, `runs[].canonical_run_dir`) to collect all required run artifacts.

### Gate-First Interpretation

Compute and report two separate suite states:

1. `operational_valid_for_ranking`
- `retry.target_met == true`
- `retry.unresolved_void_count == 0`
- `aggregate.run_count_scored >= config.repeats`

2. `quality_compliant`
- `aggregate.validity_rate == 1.0`
- every scored run has `run_valid == true`
- every scored run has `performance_gates_passed == true`

Ranking rule:
- If `operational_valid_for_ranking == false`:
  - mark suite status `INVALID_FOR_RANKING`
  - set final ranking score to `0.0`
- If `operational_valid_for_ranking == true`:
  - mark suite status `RANKABLE`
  - compute ranking score normally, regardless of `quality_compliant`
  - still report `quality_compliant` and all failure reasons

Always include both fields per suite in the scoring breakdown and per-agent insights.

### Deterministic Ranking Score (v2)

For suites with `operational_valid_for_ranking == true`, compute:

1. Positive contribution block:

`positive_score = 100 * (0.30*objective_quality + 0.20*requirements_quality + 0.20*run_validity_strength + 0.10*performance_strength + 0.10*reliability + 0.05*speed + 0.05*cost)`

2. Negative penalty block:

`penalty_score = 100 * (0.35*void_penalty + 0.25*run_validity_penalty + 0.25*performance_penalty + 0.15*requirements_penalty)`

3. Final score:

`final_score = clamp(positive_score - penalty_score, 0, 100)`

For suites with `operational_valid_for_ranking == false`:
- set `final_score = 0.0`

Metric definitions:
- `raw_objective_quality = aggregate visual objective mean`
  - For homepage tasks, this is `odiff similarity mean` from run scorecards `scores.visual.similarity`.
  - If available, treat this as region-weighted similarity (global + region blend produced by verifier).
- `raw_objective_global_quality` (optional) = aggregate mean of `scores.visual.global_similarity` when present.
  - Use this only for diagnostics to explain whether variance comes from global frame or region weighting.
- `objective_threshold = task visual threshold` (from task YAML `visual.threshold`; default to `0.95` if unavailable).
- `objective_similarity_margin = clamp((raw_objective_quality - objective_threshold) / (1 - objective_threshold), 0, 1)`
  - This expands meaningful separation above threshold so small raw ODiff deltas produce larger score variance.
- `objective_quality = objective_similarity_margin * requirements_quality`
  - This keeps requirement-missing implementations from retaining high objective contribution even when raw ODiff is close.
- `requirements_quality = mean(requirements.presence_ratio)` across scored runs.
- `run_validity_strength = aggregate.validity_rate`.
- `performance_strength = aggregate.performance_pass_rate`.
- `reliability = 1 - (void_count / aggregate.run_count_total)`.
- `speed = inverse_normalized(aggregate.duration_sec.mean)` (normalize across compared rankable suites only).
- `cost = inverse_normalized(aggregate.uncached_input_tokens.mean)` (normalize across compared rankable suites only).
- `void_penalty = void_count / aggregate.run_count_total`.
- `run_validity_penalty = 1 - aggregate.validity_rate`.
- `performance_penalty = 1 - aggregate.performance_pass_rate`.
- `requirements_penalty = 1 - mean(requirements.mapping_ratio)` across scored runs.

When any metric input is missing:
- list the missing artifact path(s),
- set that metric component to `0.0`,
- continue scoring with remaining components.

### Output UX Model (Layered)

Do not use one single dense top-level table. Use a layered output model that keeps decision speed high while preserving diagnostics.

#### Table A: `Top-Level Ranking Snapshot` (decision-first)

Purpose: fast comparison of rankable suites only.

Rows:
- Include only suites where `operational_valid_for_ranking == true`.
- Sort by `final_score` descending.

Columns (in this order):
1. `rank`
2. `harness`
3. `model`
4. `final_score`
5. `performance_pass_rate`
6. `requirements_test_mapping_rate`
7. `objective_odiff_mean`
8. `quality_compliant`
9. `quick_failure_summary`

Rules:
- Assume rows in this table are operationally rankable by definition; do not repeat `operational_valid_for_ranking` in every row.
- `quick_failure_summary` must be one short phrase:
  - `clean` when `quality_compliant == true`
  - otherwise summarize dominant failure mode(s) with compact counts.

#### Table B: `Operational Exceptions` (only non-rankable suites)

Purpose: isolate rankability blockers without polluting top-level comparison.

Rows:
- Include only suites where `operational_valid_for_ranking == false`.

Columns (in this order):
1. `harness`
2. `model`
3. `status`
4. `scored_runs/repeats`
5. `void_rate`
6. `operational_fail_reasons`
7. `missing_required_artifact_count`

#### Table C: `Detailed Diagnostic Table` (full depth)

Purpose: troubleshooting and auditability for every latest suite.

Rows:
- Include all latest suites, including non-rankable suites.

Columns (in this order):
1. `rank`
2. `harness`
3. `model`
4. `status`
5. `operational_valid_for_ranking`
6. `quality_compliant`
7. `scored_runs/repeats`
8. `void_rate`
9. `run_valid_rate`
10. `performance_pass_rate`
11. `required_verification_exec_rate`
12. `objective_odiff_mean`
13. `objective_odiff_pass_rate`
14. `objective_odiff_global_mean` (optional; `n/a` when unavailable)
15. `objective_odiff_min`
16. `requirements_test_mapping_rate`
17. `requirements_presence_rate`
18. `duration_mean_sec`
19. `uncached_input_tokens_mean`
20. `+objective_impact`
21. `+requirements_impact`
22. `+run_validity_impact`
23. `+performance_impact`
24. `+reliability_impact`
25. `+speed_impact`
26. `+cost_impact`
27. `-void_penalty_impact`
28. `-run_validity_penalty_impact`
29. `-performance_penalty_impact`
30. `-requirements_penalty_impact`
31. `final_score`
32. `top_failure_modes`

Derivations and consistency rules:
- `required_verification_exec_rate`: mean of `executed_required_verification_commands / required_verification_commands` from run metadata process block (treat required=0 as 1.0).
- `objective_odiff_pass_rate`: share of scored runs where visual threshold is met.
- `top_failure_modes`: top 3 failing checks with counts across run-validity + performance checks.
- Normalize failure-mode labels in reporting:
  - render `no_requirement_test_gaps` as `requirement_test_gaps` for readability (historical artifact compatibility).
- Each `+..._impact` and `-..._penalty_impact` column must show the signed numeric contribution used in score computation.

### Signal Direction Rules

Ensure success/failure direction is explicit and consistent:

1. In `Top-Level Ranking Snapshot`, all numeric columns must be "higher is better" signals except `final_score` already composite.
2. Keep lower-is-better metrics (`void_rate`, `duration_mean_sec`, `uncached_input_tokens_mean`) out of the top-level snapshot and in secondary tables.
3. When lower-is-better metrics are shown, label them explicitly with wording that indicates lower is better.
4. Never mix unlabeled positive and inverse interpretations in the same table.

### Redundancy Rules

1. Do not repeat constant cross-suite context per row (task/version); place it once in the report subtitle.
2. Do not repeat operational validity booleans in the top-level table where only rankable suites are shown.
3. Keep deep-dive fields in diagnostic sections; do not force all readers through full diagnostic density.

### Report Subtitle Requirement

Directly under the report title, include:
- `Scope: <task_name>@<task_version>` for this analysis set.

Assumption:
- Analysis is single-task latest-suite comparison.
- If more than one `(task_name, task_version)` appears, explicitly call this out in `Contradictions and Knock-On Effects`.

### Required Sections

Return:
1. `## Ranked Agents (Latest Suite Per Task Version)`
2. `## Scoring Breakdown`
3. `## Reliability and Failure Anatomy`
4. `## Per-Agent Insights`
5. `## Ranked Recommendations (Exhaustive)`
6. `## Suggested Experiment Backlog`
7. `## Contradictions and Knock-On Effects`
8. `## UX Rationale (Before vs After)`

`UX Rationale (Before vs After)` must be short and concrete:
- `Before`: what made quick scanning hard.
- `After`: what changed in layout and why it improves at-a-glance comprehension.
- `Tradeoff`: what information moved to deep-dive sections and why.

### Optional Skill Integration: `visual-explainer`

This section is optional and must not change the default markdown behavior when the skill is unavailable.

Skill detection:
- Check for `visual-explainer` skill presence in any of these locations:
  - `$CODEX_HOME/skills/visual-explainer/SKILL.md`
  - `~/.codex/skills/visual-explainer/SKILL.md`
  - `~/.agents/skills/visual-explainer/SKILL.md`

If skill is present:
1. Load the `visual-explainer` skill and follow its workflow.
2. Reuse the exact computed metrics from this analysis (do not recompute with different formulas).
3. Before generating visuals, build a verification fact sheet listing every numeric/table claim and its artifact source path (`suite-summary.json`, `suite.json`, `run.json`, verifier artifacts).
4. Generate a self-contained HTML companion report that explains results visually with:
   - Executive summary and ranking outcome
   - Top-level ranking snapshot + operational exceptions + detailed diagnostic table
   - Positive vs negative impact breakdown (`+..._impact`, `-..._penalty_impact`)
   - Reliability/failure anatomy and top failure modes
   - Per-agent quality vs operational state (`operational_valid_for_ranking`, `quality_compliant`)
5. Save HTML output under `./.enaible/artifacts/visual-report/` with a deterministic datetime-stamped filename (for example `eval-analysis-<task>-<YYYYMMDD-HHMMSS>.html`). Create the directory if it does not exist.
6. Return both:
   - The standard markdown analysis (all required sections above)
   - The HTML output path as an additional artifact

If skill is not present:
- Output the standard markdown analysis only, exactly as defined in this prompt.
- Do not fail, skip, or alter scoring behavior due to missing skill.

### Hard Constraints

1. Never treat deterministic-check failures as harness defects.
2. Always separate orchestrator implementation failures from task scoring failures.
3. Never propose relaxing thresholds, deterministic checks, or scoring criteria.
4. If evidence is missing, list missing artifact paths and continue with available evidence.
5. Recommendations must explicitly reference the affected task version(s) (for example `homepage-implementation@v001`).
