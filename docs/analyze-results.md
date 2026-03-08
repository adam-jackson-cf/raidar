# Analyze Results

Use this guide to analyze the latest experiment for each `(scenario_name, scenario_revision, agent, model, evaluation_profile)` combination.

## Canonical Inputs

- Experiment records: `experiments/*/experiment.json`
- Experiment summaries: `experiments/*/experiment-summary.json`
- Experiment reports: `experiments/*/report.md`
- Run records: `experiments/*/runs/*/run.json`
- Run reports: `experiments/*/runs/*/report.md`
- Verifier scorecards: `experiments/*/runs/*/verifier/scorecard.json`
- Execution-validity artifacts: `experiments/*/runs/*/verifier/execution-validity.json`
- Performance-gates artifacts: `experiments/*/runs/*/verifier/performance-gates.json`
- Agent traces: `experiments/*/runs/*/agent/*.trajectory.json`
- Agent logs: `experiments/*/runs/*/agent/*.txt`

Do not read from legacy `evals/` roots.

## Identity Rules

For each unique `(scenario_name, scenario_revision, agent, model, evaluation_profile)`:

1. Read identity from `experiment-summary.json.config`.
2. Select the latest experiment by `created_at_utc`.
3. Rank only that latest experiment.

Treat `evaluation_profile` plus `metrics` as the capability identity for comparisons.

## Required Fields

Always report:

- `experiment-summary.json.config.evaluation_profile`
- `experiment-summary.json.config.metrics`
- `experiment-summary.json.aggregate.metric_outcomes`
- `run.json.config.evaluation_profile`
- `run.json.scores.metric_results[]`

## Status Model

Compute and report both:

- Quality status: based on functional, acceptance, visual, and verification-stability outcomes.
- Ranking status: based on execution-validity and resource-efficiency outcomes.

If any run fails execution validity, mark the experiment `INVALID_FOR_RANKING`.
If all scored runs pass execution validity, mark the experiment `RANKABLE`.

## Metric Renames

Use only the migrated metric names:

- `acceptance`
- `verification-stability`
- `execution-validity`
- `resource-efficiency`
- `test-coverage`
- `requirements-coverage`
- `visual-regression`
- `artifact-checks`

## Reporting Expectations

- Use `scenario_name@scenario_revision` as the scope label.
- Attribute every numeric claim to an artifact path.
- Separate orchestrator defects from scenario acceptance failures.
- Treat `artifact-checks` as audit-only unless the experiment contract explicitly makes them gating.

## Output Artifact

Write any derived human review as:

- `experiments/eval-analysis-<scenario>-<YYYYMMDD-HHMMSS>.html`

Create the directory if needed.
