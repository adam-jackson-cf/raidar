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
- `objective_quality = aggregate visual objective mean`
  - For homepage tasks, this is `odiff similarity mean` (from run scorecards `scores.visual.similarity` across scored runs).
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

### Score Table Columns (Expanded)

Return an expanded comparison table with one row per latest suite and these columns in this order:
1. `rank`
2. `task`
3. `harness`
4. `model`
5. `status` (`RANKABLE` or `INVALID_FOR_RANKING`)
6. `operational_valid_for_ranking`
7. `quality_compliant`
8. `scored_runs/repeats`
9. `void_rate`
10. `run_valid_rate`
11. `performance_pass_rate`
12. `required_verification_exec_rate`
13. `objective_odiff_mean`
14. `objective_odiff_pass_rate`
15. `objective_odiff_min`
16. `requirements_presence_rate`
17. `requirements_test_mapping_rate`
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

Column derivations:
- `required_verification_exec_rate`: mean of `executed_required_verification_commands / required_verification_commands` from run metadata process block (treat required=0 as 1.0).
- `objective_odiff_pass_rate`: share of scored runs where visual threshold is met.
- `top_failure_modes`: top 3 failing checks with counts across run-validity + performance checks.
- Each `+..._impact` and `-..._penalty_impact` column must show the signed numeric contribution used in score computation.

### Required Sections

Return:
1. `## Ranked Agents (Latest Suite Per Task Version)`
2. `## Scoring Breakdown`
3. `## Reliability and Failure Anatomy`
4. `## Per-Agent Insights`
5. `## Ranked Recommendations (Exhaustive)`
6. `## Suggested Experiment Backlog`
7. `## Contradictions and Knock-On Effects`

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
   - Expanded score table (all required columns)
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
