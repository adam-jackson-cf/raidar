# Scorers And Metrics

Use this reference when choosing scenario scoring or interpreting experiment artifacts. Scorers are the authored scenario contract; metrics are the measured signals inside each scorer.

## Terminology

- A `scorer` is a reusable delivery-task scoring definition registered by code under `orchestrator/src/raidar/scorers/`.
- A scenario attaches scorers with `scorers[]`, and each attached scorer has a positive scenario-level `weight`.
- A `metric` is a weighted signal inside a scorer. Metrics are executed once per run even when multiple attached scorers reuse the same metric.
- Active scorer definitions are executable. Proposed scorer definitions document the catalog direction but scenario validation rejects them until their missing metrics are implemented.
- Scenario YAML is strict: removed fields such as top-level `metrics`, top-level `score_profile`, and `acceptance.llm_judge_rubric` fail validation.
- `verification.min_quality_score` requires at least one quality-category scorer. Efficiency-only scenarios must set `min_quality_score: 0.0`.

## Scenario Example

```yaml
scorers:
  - id: design-to-code
    version: 1
    weight: 0.9
    config:
      artifact-checks:
        required_paths:
          - src/app/page.tsx
          - src/components/**/*.tsx
        path_match: glob

  - id: resource-efficiency
    version: 1
    weight: 0.1
```

The resulting `evaluation_profile` is scorer-based:

`scorers:design-to-code@1:0.9+resource-efficiency@1:0.1`

## Active Scorers

| Scorer | Category | Use when | Metrics |
| --- | --- | --- | --- |
| `design-to-code@1` | `quality` | A scenario asks a harness to implement a design against visual and product evidence. | `visual-regression` 0.34, `functional` 0.24, `test-coverage` 0.15, `artifact-checks` 0.17, `verification-stability` 0.10 |
| `typescript-code-task@1` | `quality` | A TypeScript delivery task should be judged on correctness, code quality, tests, artifacts, and stable verification. | `functional` 0.30, `code-quality` 0.25, `test-coverage` 0.20, `artifact-checks` 0.15, `verification-stability` 0.10 |
| `requirements@1` | `quality` | Requirements adherence should be judged by deterministic requirement coverage plus a scorer-owned judge role. | `requirements-coverage` 0.35, `requirements-adherence` 0.65 |
| `resource-efficiency@1` | `efficiency` | Cost, token usage, command count, and verification churn should contribute to the final comparison. | `resource-efficiency` 1.00 |

## Proposed Scorers

These definitions exist as catalog entries but are not executable scenario refs yet:

- `plan-to-code@1`
- `bugfix@1`
- `code-task@1`
- `python-code-task@1`
- `refactor@1`
- `test-generation@1`

## Metric Catalog

| Metric | What it measures | Requires |
| --- | --- | --- |
| `functional` | Whether the run completed the expected build/test workflow successfully. | Scenario verification commands or gates that represent the delivery workflow. |
| `acceptance` | Whether deterministic acceptance checks pass. | `acceptance.deterministic_checks` and/or `acceptance.requirements`. |
| `code-quality` | Whether code-task static quality checks and language-specific quality rules pass. | A code-task scorer such as `typescript-code-task`. |
| `verification-stability` | How noisy or repeat-failure-prone verification gates were. | Meaningful `verification.gates`. |
| `execution-validity` | Whether the run is valid for ranking. | No scenario-specific config; derived from completion, required commands, execution health, and workflow validity. |
| `resource-efficiency` | Token, command, failure, and verification-round efficiency. | Process and trace metrics from the run. |
| `test-coverage` | Whether measured test coverage meets the scenario threshold. | `verification.coverage_threshold`. |
| `requirements-coverage` | Whether stated requirements are present and mapped to tests. | Non-empty `acceptance.requirements`. |
| `visual-regression` | Similarity to the visual reference and whether the threshold was met. | `visual.reference_image`, `visual.screenshot_command`, and visual scoring config. |
| `artifact-checks` | Whether required files or path patterns exist in the run workspace. | `artifact-checks.config.required_paths`. |
| `plan-quality` | Subjective plan-quality review using a scorer-owned judge role file. | `plan-quality.config.judge`, pointing to a judge role file under `orchestrator/src/raidar/scorers/definitions/`. |
| `requirements-adherence` | Subjective requirements-adherence review using a scorer-owned judge role file. | `requirements-adherence.config.judge`, pointing to a judge role file under `orchestrator/src/raidar/scorers/definitions/`. |

## LLM-As-Judge Files

`llm-as-judge` is a metric type. The metric id for the proposed `plan-to-code` scorer is `plan-quality`.

Judge role files are scorer-owned, not scenario-owned. A code-backed scorer definition points to one file under `orchestrator/src/raidar/scorers/definitions/`, for example:

```yaml
- id: plan-quality
  type: llm-as-judge
  weight: 0.35
  config:
    judge: judges/plan-judge.toml
```

That file contains the judge role, responsibilities, rubric, and expected output contract. Runtime validation fails if the file is missing.
Judge file paths must be relative to scorer definitions and cannot use absolute paths or parent traversal. Scenario YAML cannot override judge files.

## Artifact Fields

Run scorecards include:

- `scores.metric_scores[]`: canonical scalar outputs for every resolved metric.
- `scores.metric_scores[].judge_output`: structured judge details for judge-backed metrics, including findings and rubric coverage.
- `scores.scorer_results[]`: scorer id, version, category, scenario weight, score, and metric contributions.
- `scores.quality_score`: weighted output from quality-category scorer results only.
- `scores.composite_score`: weighted output across all scorer results after unscored and execution-validity gating.

Experiment summaries aggregate both `aggregate.metric_outcomes` and `aggregate.scorer_outcomes`.
