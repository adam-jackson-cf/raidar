# Scorers And Metrics

Use this reference when choosing scenario scoring or interpreting experiment artifacts. Scorers are the authored scenario contract; metrics are the measured signals inside each scorer.

## Terminology

- A `scorer` is a reusable delivery-task scoring definition registered by code under `orchestrator/src/raidar/scorers/`.
- A scenario attaches scorers with `scorers[]`, and each attached scorer has a positive scenario-level `weight`.
- A `metric` is a weighted signal inside a scorer. Metrics are executed once per run even when multiple attached scorers reuse the same metric.
- Scorer requirements are concrete capability inventory needed by the scorer implementation. They use the same `runtimes`, `package_managers`, `tools`, and `browsers` categories as scenario environments.
- Capability requirements list available tools only. The scorer implementation and metric definitions define behavior.
- Scenario YAML is strict. Unsupported top-level fields fail validation.
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
| `design-to-code@1` | `quality` | A scenario asks a harness to implement a design against visual and product evidence. | `visual-regression` 0.34, `functional` 0.24, `test-coverage` 0.15, `verification-stability` 0.10, `artifact-checks` 0.17 |
| `typescript-code-task@1` | `quality` | A TypeScript delivery task should be judged on correctness, code quality, tests, artifacts, and stable verification. | `functional` 0.30, `code-quality` 0.25, `test-coverage` 0.20, `artifact-checks` 0.15, `verification-stability` 0.10 |
| `python-code-task@1` | `quality` | A Python delivery task should be judged on correctness, code quality, tests, artifacts, and stable verification. | `functional` 0.30, `code-quality` 0.25, `test-coverage` 0.20, `artifact-checks` 0.15, `verification-stability` 0.10 |
| `bugfix@1` | `quality` | A targeted defect fix should be judged on resolution, regression protection, containment, verification stability, and retained defect evidence. | `defect-resolution` 0.30, `regression-protection` 0.25, `change-containment` 0.20, `verification-stability` 0.15, `defect-evidence-completeness` 0.10 |
| `refactor@1` | `quality` | A refactor should preserve behavior while improving structure and keeping public contracts stable. | `behavior-preservation` 0.30, `structural-improvement` 0.25, `public-contract-stability` 0.15, `change-containment` 0.15, `verification-stability` 0.15 |
| `test-generation@1` | `quality` | A task asks the harness to add or improve tests without hiding behavior changes in production code. | `requirement-mapping` 0.25, `assertion-strength` 0.25, `coverage-lift` 0.25, `production-code-guardrail` 0.15, `verification-stability` 0.10 |
| `plan-to-code@1` | `quality` | Implementation should be judged against an approved plan and retained acceptance evidence. | `plan-adherence` 0.35, `planned-scope-coverage` 0.25, `acceptance-evidence-completeness` 0.20, `functional` 0.10, `verification-stability` 0.10 |
| `requirements@1` | `quality` | Requirements adherence should be judged by deterministic requirement coverage plus a scorer-owned judge role. | `requirements-coverage` 0.35, `requirements-adherence` 0.65 |
| `resource-efficiency@1` | `efficiency` | Cost, token usage, command count, and verification churn should contribute to the final comparison. | `resource-efficiency` 1.00 |

## Scorer Runtime Requirements

Scorers may declare capability requirements when they need concrete tooling in the task image. For example, `python-code-task@1` requires:

```yaml
runtimes:
  python: ">=3.12"
tools:
  ruff: ">=0.14"
  pytest: ">=9"
  coverage: ">=7"
  lizard: ">=1.17"
```

Scenario environment requirements, verifier runner requirements, harness execution requirements, and scorer requirements are merged into the effective run contract and checked against the resolved image capabilities.

Do not model scorer behavior as a capability. Coverage, linting, visual comparison, plan adherence, and requirement adherence are metrics or scorer actions. Tools such as `coverage`, `ruff`, `odiff`, `playwright`, and `git` are capabilities.

## Metric Catalog

| Metric | What it measures | Requires |
| --- | --- | --- |
| `functional` | Whether the run completed the expected build/test workflow successfully. | Scenario verification commands or gates that represent the delivery workflow. |
| `code-quality` | Whether code-task static quality checks and language-specific quality rules pass. | A code-task scorer such as `typescript-code-task` or `python-code-task`. |
| `verification-stability` | How noisy or repeat-failure-prone verification gates were. | Meaningful `verification.gates`. |
| `execution-validity` | Whether the run is valid for ranking. | No scenario-specific config; derived from completion, required commands, execution health, and workflow validity. |
| `resource-efficiency` | Token, command, failure, and verification-round efficiency. | Process and trace metrics from the run. |
| `test-coverage` | Whether measured test coverage meets the scenario threshold. | `verification.coverage_threshold`. |
| `requirements-coverage` | Whether stated requirements are present and mapped to tests. | Non-empty `requirements.items`. |
| `requirements-adherence` | Subjective requirements review using a scorer-owned judge role file. | Scorer-owned judge file under `orchestrator/src/raidar/scorers/definitions/`. |
| `visual-regression` | Similarity to the visual reference and whether the threshold was met. | `visual.reference_image`, `visual.screenshot_command`, `visual.artifact_manifest`, and visual scoring config. |
| `artifact-checks` | Whether required files or path patterns exist in the run workspace. | `artifact-checks.config.required_paths`. |
| `plan-adherence` | Subjective plan-adherence review using a scorer-owned judge role file. | Scorer-owned `judges/plan-judge.toml` and retained plan evidence. |
| `defect-resolution` | Whether a defect-linked behavior is fixed and functionally passing. | Requirements or retained evidence that identify the defect behavior. |
| `regression-protection` | Whether the fix adds behavior-specific regression protection. | Added or changed tests near the defect behavior. |
| `change-containment` | Whether changes stay inside the expected delivery surface. | Workspace diff metadata and expected path context where configured. |
| `defect-evidence-completeness` | Whether defect, regression, verification, and changed-file evidence is retained. | Scenario-declared retained evidence or equivalent run metadata. |
| `behavior-preservation` | Whether behavior remains intact during a refactor. | Final functional execution evidence. |
| `structural-improvement` | Whether structure improves or avoids regression. | Source inventory and language-specific analysis when available. |
| `public-contract-stability` | Whether public API or behavior-bearing contracts stay stable. | Public surface files and tests covering public behavior. |
| `planned-scope-coverage` | Whether approved plan items are delivered with evidence. | Retained plan packet evidence. |
| `acceptance-evidence-completeness` | Whether planned acceptance rows include valid passing evidence. | Retained acceptance tracker evidence. |
| `requirement-mapping` | Whether generated tests map to stated requirements or changed behavior. | Scenario requirements and changed or added tests. |
| `assertion-strength` | Whether generated tests assert behavior meaningfully. | Test files with assertions and no skipped/focused shortcuts. |
| `coverage-lift` | Whether generated tests improve or satisfy coverage. | Starter/final coverage evidence where available. |
| `production-code-guardrail` | Whether test-generation work avoids unapproved production changes. | Workspace diff metadata and allowed production edit context. |

## LLM-As-Judge Files

`llm-as-judge` is a metric type. The metric ids are `plan-adherence` and `requirements-adherence`.

Judge role files are scorer-owned, not scenario-owned. A code-backed scorer definition points to one file under `orchestrator/src/raidar/scorers/definitions/`, for example:

```yaml
- id: plan-adherence
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
